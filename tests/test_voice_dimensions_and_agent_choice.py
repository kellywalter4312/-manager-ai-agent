"""Voice dimensions kept apart, and who decides (2026-09-21).

Found at runtime:
- "Speak Italian" was confirmed ("Done - speaking Italian") while English was
  said: no translator was enabled to put replies into Italian;
- a language/accent request pinned the voice that satisfied it as the
  person's own explicit override, and switched a male voice to a female one;
- "Speak Brazilian Portuguese" / "use a British English dialect" were not
  understood: a language's regional variety was not a dimension at all;
- "speak in a calm style" was ignored rather than answered;
- a specialist (Arthur) could not choose its own voice.

Held here: language, accent, variant, style and pace are separate; a provider
that cannot do something says so; the agent chooses, the person overrides,
the provider supplies - for every agent.
"""

from __future__ import annotations

import json

import pytest

import scripts.control_center as cc
import scripts.serve_ui as su
from scripts.exchange import exchange
from src.config.settings import Settings
from src.providers.availability import Availability, AvailabilityState
from src.services.identity.voice_configuration import parse_voice_configuration, plan_voice, voice_facts, voice_setting_for
from src.services.identity.voice_options import VoiceOption
from src.services.identity.voice_preference import autonomous_voice_choice_for, voice_preference_for


def _v(voice_id, language, locale, gender):
    return VoiceOption(voice_id=voice_id, name=voice_id, provider="scripted", language=language, locale=locale, gender=gender)


VOICES = (
    _v("en-gb-m", "english", "en-GB", "male"), _v("en-us-m", "english", "en-US", "male"),
    _v("en-us-f", "english", "en-US", "female"), _v("it-m", "italian", "it", "male"),
    _v("it-f", "italian", "it", "female"), _v("pt-br-m", "portuguese", "pt-BR", "male"),
)


class _Speaker:
    """A speech engine at its own boundary: real facts, bytes for synthesis."""

    name = "scripted"
    speaks_across_languages = True

    def __init__(self, styles=()):
        self._styles = styles

    @property
    def availability(self):
        return Availability(state=AvailabilityState.AVAILABLE)

    def available_voices(self):
        return VOICES

    def spoken_languages(self):
        return ("english", "italian", "portuguese")

    def pace_words(self):
        return ("slow", "normal", "fast")

    def speaking_styles(self):
        return self._styles

    def speak(self, text, voice=None):
        return True

    def listen(self, timeout=None):
        raise NotImplementedError

    def synthesize(self, text, voice=None):
        return b"RIFF" + text.encode("utf-8")

    def voice_in_use(self, voice):
        return (voice.style or {}).get("scripted_voice_id") or voice.preferred_voice


class TestTheDimensionsAreParsedApart:
    @pytest.mark.parametrize("text,language,accent,variant", [
        ("Speak English with an Italian accent", "english", "italian", None),
        ("Speak Italian", "italian", None, None),
        ("Speak Brazilian Portuguese", "portuguese", None, "brazilian"),
        ("Please use a British English dialect", "english", None, "british"),
        ("Use a Nigerian English voice if one is available", None, None, None),
    ])
    def test_language_accent_and_variant(self, text, language, accent, variant):
        parsed = parse_voice_configuration(text)
        assert (parsed.language, parsed.accent, parsed.variant) == (language, accent, variant)

    def test_a_style_is_its_own_dimension(self):
        parsed = parse_voice_configuration("Speak in a calm, warm style")
        assert parsed.style == "calm warm" and parsed.language is None and parsed.accent is None


class TestPlanning:
    facts = voice_facts(_Speaker())

    def test_a_variant_is_a_native_voice_of_that_region(self):
        plan = plan_voice(self.facts, current=VOICES[1], variant="british", language="english")
        assert plan.ok and plan.voice.voice_id == "en-gb-m" and plan.variant == "british"

    def test_a_missing_variant_is_refused_naming_what_exists(self):
        plan = plan_voice(self.facts, current=VOICES[1], variant="australian", language="english")
        assert not plan.ok and "American" in plan.message and "British" in plan.message

    def test_an_accent_keeps_the_kind_of_voice(self):
        plan = plan_voice(self.facts, current=VOICES[1], accent="italian", language="english")
        assert plan.ok and plan.voice.voice_id == "it-m"

    def test_style_is_refused_when_the_provider_has_none_and_used_when_it_does(self):
        assert not plan_voice(self.facts, current=VOICES[1], style="calm").ok
        styled = voice_facts(_Speaker(styles=("calm", "cheerful")))
        plan = plan_voice(styled, current=VOICES[1], style="calm")
        assert plan.ok and plan.style == "calm"


@pytest.fixture
def system(tmp_path, monkeypatch):
    for name, value in (
        ("DEFAULT_ACCOUNTS_PATH", tmp_path / "accounts.json"), ("DEFAULT_CONFIG_PATH", tmp_path / "config.json"),
        ("DEFAULT_LEDGER_PATH", tmp_path / "state.json"), ("DEFAULT_STAGING_DIR", tmp_path / "staging"),
        ("DEFAULT_BACKUPS_DIR", tmp_path / "backups"),
    ):
        monkeypatch.setattr(cc, name, value)
    live = su.build_live_system(Settings.from_env(
        env={}, memory="ephemeral", profile_path="does-not-exist-USER.md",
        artifacts_dir=str(tmp_path / "artifacts"), media_asset_dir=str(tmp_path / "assets"),
        knowledge_dir=str(tmp_path / "knowledge"), documents_dir=str(tmp_path / "docs"),
    ))
    live.channel._speech = _Speaker()
    return live


def _say(system, text):
    return exchange(system.channel, system.conversation, text)


class TestThroughTheServedSystem:
    def test_a_language_without_a_translator_is_not_claimed(self, system):
        outcome = _say(system, "Speak Italian").outcome
        assert "can't speak it yet" in outcome and "Done" not in outcome
        manager = system.manager.own_skills.name
        spoken = json.loads(su.route_speech(manager, "The train leaves at eight.", system)[1])
        assert spoken["note"].startswith("said as written")

    def test_a_language_request_is_the_agents_choice_not_a_pinned_override(self, system):
        name = system.manager.own_skills.name
        _say(system, "Speak English with an Italian accent")
        assert voice_preference_for(name, recall=system.manager.memory.recall) is None
        choice = autonomous_voice_choice_for(name, recall=system.manager.memory.recall)
        assert choice is not None and choice.voice_id.startswith("it-")
        assert voice_setting_for(name, "accent", recall=system.manager.memory.recall) == "italian"

    def test_a_variant_is_stored_and_spoken_in(self, system):
        name = system.manager.own_skills.name
        assert "British English" in _say(system, "Use a British English dialect").outcome
        assert voice_setting_for(name, "variant", recall=system.manager.memory.recall) == "british"
        spoken = json.loads(su.route_speech(name, "Good morning.", system)[1])
        assert spoken["voice"] == "en-gb-m"

    def test_a_style_request_is_answered_honestly(self, system):
        assert "no speaking styles" in _say(system, "Speak in a calm, warm style").outcome

    def test_arthur_chooses_and_the_person_overrides(self, system):
        arthur = system.manager.registry.get("Arthur")
        _say(system, "Arthur, choose the voice that best suits you")
        chosen = autonomous_voice_choice_for("Arthur", recall=arthur.memory.recall)
        assert chosen is not None
        cc.run_command(system.centre, "voice Arthur en-us-f", 100, system.channel, system.conversation)
        spoken = json.loads(su.route_speech("Arthur", "Two new messages.", system)[1])
        assert spoken["voice"] == "en-us-f"
        # Alfred's own voice is untouched by any of it
        assert voice_preference_for(system.manager.own_skills.name, recall=system.manager.memory.recall) is None
