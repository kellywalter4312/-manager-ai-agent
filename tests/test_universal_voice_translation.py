"""Post-Pre-Stage-9 D2 - Alfred Universal Voice Translation.

Unit tests use in-process doubles for the translator, listener and voice; the
offline translator is also exercised against the real installed language packs
when they are present (no network is reached - the suite blocks it). Live speech
through real models is verified outside the suite and recorded in ARCHITECTURE.md.
"""

from __future__ import annotations

import base64
import json
from dataclasses import dataclass, replace

import pytest

import scripts.control_center as cc
import scripts.serve_ui as su
from scripts.exchange import exchange
from src.config.settings import Settings
from src.core.contracts import Task
from src.manager.capabilities import CapabilityRequest, translate
from src.services.translation.interpreter import InterpretationError, Interpreter
from src.services.translation.languages import guess_language, language_by_code, language_named


@dataclass(frozen=True)
class _Result:
    text: str
    source: str
    target: str
    route: tuple


class FakeTranslator:
    name = "fake"

    def __init__(self, pairs=(("en", "es"), ("es", "en"), ("en", "fr"), ("fr", "en"))):
        self._pairs = tuple(pairs)
        self.calls = []

    def pairs(self):
        return self._pairs

    def languages(self):
        return tuple(sorted({code for pair in self._pairs for code in pair}))

    def can_translate(self, source, target):
        return source == target or (source, target) in self._pairs or (
            (source, "en") in self._pairs and ("en", target) in self._pairs
        )

    def translate(self, text, source, target):
        self.calls.append((text, source, target))
        return _Result(f"[{target}] {text}", source, target, (source, target))


@dataclass(frozen=True)
class _Heard:
    text: str
    language: str
    probability: float = 0.98
    seconds: float = 1.5


class FakeListener:
    name = "fake_listener"

    def __init__(self, heard):
        self.heard = heard

    def recognise(self, audio):
        assert audio
        return self.heard


@dataclass(frozen=True)
class _Spoken:
    wav: bytes
    voice: str
    seconds: float


class FakeVoice:
    name = "fake_voice"

    def can_speak(self, language):
        return language.code in ("en", "es")

    def synthesize(self, text, language):
        return _Spoken(b"RIFF" + text.encode("utf-8"), f"{language.code}_voice", 1.0)


def _interpreter(heard=None):
    return Interpreter(FakeTranslator(), FakeListener(heard) if heard else None, FakeVoice())


class TestLanguages:
    @pytest.mark.parametrize("name, code", [("Spanish", "es"), ("español", "es"), ("FR", "fr"), ("mandarin", "zh")])
    def test_a_language_is_found_by_how_a_person_names_it(self, name, code):
        assert language_named(name).code == code

    @pytest.mark.parametrize("text, code", [
        ("¿Dónde está la estación de tren?", "es"),
        ("Où est la gare ? Je dois acheter deux billets.", "fr"),
        ("Wo ist der Bahnhof? Ich muss zwei Fahrkarten kaufen.", "de"),
        ("Dov'è la stazione? Devo comprare due biglietti.", "it"),
        ("火车站在哪里？", "zh"),
        ("Where is the station? I need two tickets.", "en"),
    ])
    def test_the_language_of_an_ordinary_sentence_is_guessed(self, text, code):
        assert guess_language(text) == code


class TestTheInterpreter:
    def test_text_is_translated_with_a_guessed_or_named_source(self):
        interpreter = _interpreter()
        assert interpreter.translate_text("¿Dónde está la estación?", "English").source == "es"
        assert interpreter.translate_text("hola", "english", "Spanish").text == "[en] hola"

    def test_speech_goes_towards_the_other_person_of_the_pair(self):
        spanish = _interpreter(_Heard("¿Dónde está la estación?", "es"))
        turn = spanish.interpret_speech(b"audio", pair=["en", "es"])
        assert (turn.source, turn.target, turn.spoken_by) == ("es", "en", "local_voice")
        assert turn.audio_wav and turn.to_dict()["audio_base64"]

        english = _interpreter(_Heard("Where is the station?", "en"))
        turn = english.interpret_speech(b"audio", pair=["en", "es"])
        assert (turn.source, turn.target) == ("en", "es")

    def test_a_language_with_no_local_voice_is_left_to_the_device_honestly(self):
        turn = _interpreter(_Heard("Where is the station?", "en")).interpret_speech(b"audio", target="French")
        assert turn.target == "fr" and turn.spoken_by == "device" and turn.audio_wav is None

    def test_the_device_voice_mode_skips_local_synthesis(self):
        turn = _interpreter(_Heard("Where is the station?", "en")).interpret_speech(b"audio", pair=["en", "es"], speak="device")
        assert (turn.target, turn.spoken_by, turn.audio_wav) == ("es", "device", None)

    def test_every_missing_piece_is_named(self):
        with pytest.raises(InterpretationError, match="video_understanding_provider"):
            _interpreter().interpret_speech(b"audio", pair=["en", "es"])
        with pytest.raises(InterpretationError, match="translation_provider"):
            Interpreter(None, None, None).translate_text("hello", "Spanish")
        with pytest.raises(InterpretationError, match="can't translate offline"):
            _interpreter(_Heard("你好", "zh")).interpret_speech(b"audio", pair=["en", "es"])
        with pytest.raises(InterpretationError, match="don't know a language"):
            _interpreter().translate_text("hello", "Klingon")

    def test_status_reports_languages_with_what_each_can_do(self):
        status = _interpreter().status()
        spanish = next(l for l in status["languages"] if l["code"] == "es")
        german = next(l for l in status["languages"] if l["code"] == "de")
        assert status["available"] and spanish["translatable"] and spanish["speakable"]
        assert not german["translatable"] and not status["listens"]


class TestTheTranslateCapability:
    def _request(self, description, *, translate_fn=None, converse=None):
        from src.services.identity.identity import AgentIdentity

        return CapabilityRequest(
            task=Task(description=description), identity=AgentIdentity(name="Alfred", capabilities=frozenset({"translate"})),
            roster=lambda: (), own_name="Alfred", own_capabilities=(), translate=translate_fn, converse=converse,
        )

    def test_half_brain_translates_offline(self):
        interpreter = _interpreter()
        result = translate(self._request("translate where is the station into Spanish", translate_fn=interpreter.translate_text))
        assert result.succeeded and result.output == "[es] where is the station"
        assert result.payload["engine"] == "offline" and result.payload["route"] == "en->es"

    def test_a_named_source_language_is_honoured(self):
        interpreter = _interpreter()
        result = translate(self._request("translate hola from Spanish into English", translate_fn=interpreter.translate_text))
        assert result.output == "[en] hola" and result.payload["source"] == "es"

    def test_an_uninstalled_pair_is_refused_honestly_without_a_brain(self):
        interpreter = _interpreter()
        result = translate(self._request("translate hello into Japanese", translate_fn=interpreter.translate_text))
        assert not result.succeeded and "won't guess" in result.error and "can't translate" in result.error


@pytest.fixture
def isolated(tmp_path, monkeypatch):
    for name, value in (
        ("DEFAULT_ACCOUNTS_PATH", tmp_path / "accounts.json"),
        ("DEFAULT_CONFIG_PATH", tmp_path / "config.json"),
        ("DEFAULT_LEDGER_PATH", tmp_path / "state.json"),
        ("DEFAULT_STAGING_DIR", tmp_path / "staging"),
        ("DEFAULT_BACKUPS_DIR", tmp_path / "backups"),
    ):
        monkeypatch.setattr(cc, name, value)
    return tmp_path


def _system(tmp_path):
    return su.build_live_system(Settings.from_env(
        env={}, memory="ephemeral", profile_path="does-not-exist-USER.md",
        artifacts_dir=str(tmp_path / "artifacts"), media_asset_dir=str(tmp_path / "assets"),
        knowledge_dir=str(tmp_path / "knowledge"), documents_dir=str(tmp_path / "docs"),
    ))


class TestWiredIntoTheServedSystem:
    def test_translation_is_off_by_default_and_every_surface_says_so(self, isolated):
        system = _system(isolated)
        status, body, _ = su.route_translate_text("hello", "Spanish", None, True, system)
        assert status == 409 and "translation_provider" in json.loads(body)["error"]
        snap = su.snapshot(system.centre, conversation=system.conversation, channel=system.channel) if hasattr(su, "snapshot") else None
        from src.control_center.snapshot import snapshot

        assert snapshot(system.centre)["translation"]["available"] is False
        assert "translation_provider" in cc.run_command(system.centre, "translation", 100, system.channel, system.conversation)
        turn = exchange(system.channel, system.conversation, "translate good morning into French")
        assert turn.capability == "translate" and "translation_provider" in turn.outcome

    def test_the_routes_translate_text_and_speech_when_an_interpreter_is_on(self, isolated):
        system = _system(isolated)
        system.runtime = replace(system.runtime, translation=_interpreter(_Heard("¿Dónde está la estación?", "es")))
        status, body, _ = su.route_translate_text("where is the station", "es", "en", True, system)
        data = json.loads(body)
        assert status == 200 and data["text"] == "[es] where is the station" and data["spoken_by"] == "local_voice"
        assert base64.b64decode(data["audio_base64"]).startswith(b"RIFF")

        audio = base64.b64encode(b"fake webm bytes").decode("ascii")
        status, body, _ = su.route_translate_speech(audio, None, ["en", "es"], True, system)
        data = json.loads(body)
        assert status == 200 and (data["source"], data["target"]) == ("es", "en") and data["audio_base64"]
        assert su.route_translate_speech("", None, ["en", "es"], True, system)[0] == 400
        assert su.route_translate_speech(audio, None, None, True, system)[0] == 400

    def test_the_control_center_category_is_registered_and_saved(self, isolated):
        system = _system(isolated)
        out = cc.run_command(system.centre, "provider translation_provider", 100, system.channel, system.conversation)
        assert "argos" in out and "null" in out
        saved = cc.run_command(system.centre, "provider translation_provider use null", 100, system.channel, system.conversation)
        assert saved.startswith("Saved"), saved


def _real_packs_installed():
    try:
        from src.providers.translation.offline import OfflineTranslator

        pairs = set(OfflineTranslator.from_installed().pairs())
    except Exception:  # noqa: BLE001
        return False
    return {("en", "es"), ("es", "en"), ("en", "fr")} <= pairs


@pytest.mark.skipif(not _real_packs_installed(), reason="offline language packs are not installed here")
class TestTheRealOfflineTranslator:
    def test_real_packs_translate_both_ways_and_pivot(self):
        from src.providers.translation.offline import OfflineTranslator

        translator = OfflineTranslator.from_installed()
        assert "estación" in translator.translate("Where is the train station?", "en", "es").text
        # Regression: Argos's own escaped BPE tokenizer looped on this pack.
        spanish = translator.translate("¿Dónde está la estación de tren?", "es", "en").text
        assert "station" in spanish.lower() and "mainstre" not in spanish
        pivot = translator.translate("¿Dónde está la estación de tren?", "es", "fr")
        assert pivot.route == ("es", "en", "fr") and "gare" in pivot.text.lower()


class TestAnUnknownSourceIsNeverEchoedBack:
    """Found by driving the real Control Center (2026-09-15): "translate
    buenas noches into English" answered "buenas noches", reported as a
    success. The guess found nothing, English was assumed, and English was
    "translated" into English. A translation that is really a passthrough is
    the worst possible failure of this capability - it looks like an answer."""

    def _interpreter(self):
        from src.providers.translation.factory import build_interpreter

        class _Settings:
            translation = "argos"

        interpreter = build_interpreter(_Settings())
        if not interpreter.translates:
            import pytest

            pytest.skip("no offline language packs are installed on this machine")
        return interpreter

    def test_a_short_real_phrase_is_recognised_and_translated(self):
        result = self._interpreter().translate_text("buenas noches", "English")
        assert result.source == "es" and result.target == "en"
        assert "buenas" not in result.text.lower()

    def test_an_unrecognisable_phrase_is_refused_with_the_way_to_fix_it(self):
        from src.services.translation.interpreter import InterpretationError

        import pytest

        with pytest.raises(InterpretationError) as raised:
            self._interpreter().translate_text("xyzzy plugh", "English")
        assert "can't tell what language" in str(raised.value)
        assert "from Spanish" in str(raised.value)

    def test_translating_something_into_its_own_language_says_so(self):
        from src.services.translation.interpreter import InterpretationError

        import pytest

        with pytest.raises(InterpretationError) as raised:
            self._interpreter().translate_text("good evening", "English")
        assert "already looks like English" in str(raised.value)

    def test_naming_the_source_still_wins_over_the_guess(self):
        result = self._interpreter().translate_text("buenas noches", "English", "Spanish")
        assert result.source == "es" and "night" in result.text.lower()
