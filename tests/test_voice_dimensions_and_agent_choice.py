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
from types import SimpleNamespace

import pytest

import scripts.control_center as cc
import scripts.serve_ui as su
from scripts.exchange import exchange
from src.config.settings import Settings
from src.providers.availability import Availability, AvailabilityState
from src.services.identity.voice_configuration import (
    parse_voice_configuration, plan_voice, voice_facts, voice_for_settings, voice_setting_for,
)
from src.services.identity.voice_options import VoiceOption
from src.services.identity.voice_preference import autonomous_voice_choice_for, voice_preference_for


def _v(voice_id, language, locale, gender):
    return VoiceOption(voice_id=voice_id, name=voice_id, provider="scripted", language=language, locale=locale, gender=gender)


VOICES = (
    _v("en-gb-m", "english", "en-GB", "male"), _v("en-us-m", "english", "en-US", "male"),
    _v("en-us-f", "english", "en-US", "female"), _v("it-m", "italian", "it", "male"),
    _v("it-f", "italian", "it", "female"), _v("pt-br-m", "portuguese", "pt-BR", "male"),
)

# A voice catalogue shaped like ElevenLabs' real one: style is a fact of a
# *specific* voice (its own "descriptive" label), not a provider-wide knob
# every voice can take on - the exact distinction U-VOICE-05's repair turns
# on. Two voices genuinely carry one; the third carries none, honestly.
STYLED_VOICES = (
    VoiceOption(voice_id="cal-1", name="Calm One", provider="scripted", gender="female", style="calm"),
    VoiceOption(voice_id="cal-2", name="Calm Two", provider="scripted", gender="male", style="calm"),
    VoiceOption(voice_id="plain-1", name="Plain One", provider="scripted", gender="female", style=None),
    VoiceOption(voice_id="cheer-1", name="Cheerful One", provider="scripted", gender="male", style="cheerful"),
)


class _StyledSpeaker:
    """A provider whose styles are per-voice facts, like ElevenLabs - as
    opposed to ``_Speaker`` below, whose ``speaking_styles()`` is a flat
    capability list unconnected to any one voice."""

    name = "scripted-styled"
    speaks_across_languages = True

    @property
    def availability(self):
        return Availability(state=AvailabilityState.AVAILABLE)

    def available_voices(self):
        return STYLED_VOICES

    def spoken_languages(self):
        return ()

    def speaking_styles(self):
        return tuple(sorted({v.style for v in STYLED_VOICES if v.style}))

    def synthesize(self, text, voice=None):
        return b"RIFF" + text.encode("utf-8")

    def speak(self, text, voice=None):
        return True

    def listen(self, timeout=None):
        raise NotImplementedError

    def voice_in_use(self, voice):
        """Name-matched, the same resolution a real name-keyed engine
        (Windows) uses - this provider's own real id-keyed style lives in
        ``preferred_voice`` after ``_apply_provider_choice`` since
        "scripted-styled" is not an id-keyed provider."""
        if voice is None:
            return None
        for v in STYLED_VOICES:
            if v.name == voice.preferred_voice:
                return v.voice_id
        return None


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
        # This provider validates the style but attributes it to no real
        # voice (the scripted double's VOICES carry no .style at all) - the
        # honest behaviour: accepted, and the voice is left exactly as it
        # was, never swapped for one that does not actually match.
        assert plan.voice is not None and plan.voice.voice_id == VOICES[1].voice_id

    def test_a_style_that_is_a_real_per_voice_fact_actually_picks_a_voice(self):
        """U-VOICE-05's real repair: when the provider's own catalogue
        attributes a style to specific voices (ElevenLabs' shape), asking
        for it must change *which voice speaks* - not just be validated
        and silently ignored."""
        styled_facts = voice_facts(_StyledSpeaker())
        plan = plan_voice(styled_facts, current=STYLED_VOICES[2], style="calm")
        assert plan.ok and plan.voice is not None
        assert plan.voice.voice_id in ("cal-1", "cal-2")
        assert plan.voice.style == "calm"

    def test_style_plus_gender_narrows_to_the_real_matching_voice(self):
        styled_facts = voice_facts(_StyledSpeaker())
        plan = plan_voice(styled_facts, current=None, style="calm", gender="male")
        assert plan.ok and plan.voice.voice_id == "cal-2"

    def test_an_unmet_style_gender_combination_is_refused_honestly(self):
        """'female' voices genuinely exist, and 'cheerful' genuinely exists
        (on cheer-1) - but no voice is both, so this is the real, later,
        style-specific refusal, not the earlier bare-gender one."""
        styled_facts = voice_facts(_StyledSpeaker())
        plan = plan_voice(styled_facts, current=None, style="cheerful", gender="female")
        assert not plan.ok
        assert "cheerful" in plan.message

    def test_style_setting_carries_to_a_real_voice_on_another_provider(self):
        """The same cross-provider carry accent/variant already get -
        voice_for_settings resolves a stored style setting against a
        different provider's real, style-bearing catalogue."""
        remembered = {}

        def recall(*, kind, tags, limit=5):
            tag = next(iter(tags))
            return [SimpleNamespace(id="1", context={"value": remembered[tag]})] if tag in remembered else []

        remembered["voice_setting:style:alfred"] = "calm"
        found = voice_for_settings(STYLED_VOICES, "alfred", recall=recall)
        assert found is not None and found.style == "calm"


@pytest.fixture
def styled_system(tmp_path, monkeypatch):
    """The served system with a provider whose voices carry real, per-voice
    style facts (ElevenLabs' own shape) - proving the U-VOICE-05 repair
    through the Control Center's own command surface, not just plan_voice
    in isolation."""
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
    live.channel._speech = _StyledSpeaker()
    return live


class TestStyleEndToEndThroughTheControlCenter:
    """U-VOICE-05, the full chain: Control Center command -> real voice
    selection -> persisted setting -> runtime speech routing -> the same
    setting read back, honestly, the way a restart would find it."""

    def test_control_center_reports_the_real_styles_this_provider_has(self, styled_system):
        listing = cc.run_command(styled_system.centre, "voice providers", 100, styled_system.channel, styled_system.conversation)
        assert "styles: calm, cheerful" in listing

    def test_a_style_request_actually_changes_who_speaks(self, styled_system):
        name = styled_system.manager.own_skills.name
        outcome = cc.run_command(styled_system.centre, f"voice {name} style calm", 100, styled_system.channel, styled_system.conversation)
        assert not outcome.startswith("FAILED")
        assert "cal-1" in outcome or "cal-2" in outcome or "Calm" in outcome

        # Persisted - the same governed Memory record every other voice
        # setting already goes through, readable exactly as a restart would
        # read it back.
        assert voice_setting_for(name, "style", recall=styled_system.manager.memory.recall) == "calm"

        # Runtime effect - the next thing actually said resolves to one of
        # the two real voices genuinely tagged "calm", never a fabricated one.
        spoken = json.loads(su.route_speech(name, "Good morning.", styled_system)[1])
        assert spoken["voice"] in ("cal-1", "cal-2")

    def test_an_unsupported_style_is_refused_honestly_never_silently_applied(self, styled_system):
        name = styled_system.manager.own_skills.name
        outcome = cc.run_command(styled_system.centre, f"voice {name} style furious", 100, styled_system.channel, styled_system.conversation)
        assert outcome.startswith("FAILED") and "furious" in outcome
        assert voice_setting_for(name, "style", recall=styled_system.manager.memory.recall) is None

    def test_the_per_voice_listing_shows_which_real_voices_carry_which_style(self, styled_system):
        """Found alongside the repair: the discovered-options listing
        already showed language/accent/locale/gender per voice but
        silently dropped style, the one fact this repair exists to make
        visible."""
        name = styled_system.manager.own_skills.name
        listing = cc.run_command(styled_system.centre, f"voice {name}", 100, styled_system.channel, styled_system.conversation)
        assert "style=calm" in listing and "style=cheerful" in listing


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
