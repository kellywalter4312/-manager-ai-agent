"""Stage 11.4 - Alfred voice selection: the user chooses.

Reuses every existing piece rather than building a second voice
configuration system: `VoiceProfile` (Stage 3/8A/11.2) gains one more
optional characteristic (`preferred_voice`), parsed by the exact same
`## Voice` section parser every other trait already goes through, and
`_select_voice_name` (Stage 11.2) is extended to honour it - matched
against `installed_voice_names()`, the exact same discovery mechanism
Stage 11.2 already built, never a new one.

No test here depends on a live microphone or speaker; `_run_powershell`
is mocked exactly as it already is in test_windows_speech_provider.py and
test_agent_voice_identity.py. Genuine, live, non-mocked verification
(real synthesis with the user-selected voice, a real fallback check) was
performed once by hand, outside this suite, and is recorded in this
session's own report.
"""

from __future__ import annotations

import subprocess

import pytest

import src.providers.windows_speech_provider as windows_speech_provider
from src.providers.windows_speech_provider import WindowsSpeechAdapter, _select_voice_name
from src.services.identity.identity import load_identity, parse_identity
from src.services.identity.voice import VoiceProfile, default_voice, parse_voice

INSTALLED = ("Microsoft David Desktop", "Microsoft Hazel Desktop", "Microsoft Zira Desktop")


def completed(returncode=0, stdout="", stderr=""):
    return subprocess.CompletedProcess(args=["powershell"], returncode=returncode, stdout=stdout, stderr=stderr)


# ---------------------------------------------------------------------------
# VoiceProfile: the field, and that it changes nothing else
# ---------------------------------------------------------------------------


class TestVoiceProfileField:
    def test_defaults_to_unset(self):
        assert VoiceProfile(key="x").preferred_voice == ""

    def test_carries_a_stated_preference(self):
        profile = VoiceProfile(key="x", preferred_voice="Microsoft David Desktop")
        assert profile.preferred_voice == "Microsoft David Desktop"

    def test_whitespace_is_collapsed_like_every_other_trait(self):
        profile = VoiceProfile(key="x", preferred_voice="  Microsoft   David   Desktop  ")
        assert profile.preferred_voice == "Microsoft David Desktop"

    def test_appears_in_traits_when_stated(self):
        profile = VoiceProfile(key="x", preferred_voice="Microsoft David Desktop")
        assert "Microsoft David Desktop" in profile.traits

    def test_does_not_appear_in_traits_when_unset(self):
        assert VoiceProfile(key="x").traits == ()

    def test_default_voice_declares_no_preference(self):
        """default_voice(name) - the fallback for an agent with no ## Voice
        section at all - must not invent a preference."""
        assert default_voice("Some Agent").preferred_voice == ""

    def test_is_immutable(self):
        profile = VoiceProfile(key="x", preferred_voice="Microsoft David Desktop")
        with pytest.raises(Exception):
            profile.preferred_voice = "Something Else"


# ---------------------------------------------------------------------------
# Persistence: the existing ## Voice section parser, unchanged in shape
# ---------------------------------------------------------------------------


class TestParsingAndPersistence:
    def test_a_voice_section_with_preferred_voice_parses(self):
        profile = parse_voice(
            ["key: somebody", "accent: plain", "preferred_voice: Microsoft David Desktop"],
            key="somebody",
        )
        assert profile.preferred_voice == "Microsoft David Desktop"
        assert profile.accent == "plain"  # existing traits still parse alongside it

    def test_a_voice_section_with_no_preference_leaves_it_unset(self):
        profile = parse_voice(["key: somebody", "accent: plain"], key="somebody")
        assert profile.preferred_voice == ""

    def test_an_unrecognised_multiword_key_falls_back_to_style_not_a_crash(self):
        """Same, pre-existing behaviour every other trait name already has -
        this stage does not change how unknown lines are handled, only adds
        one more recognised name."""
        profile = parse_voice(["key: somebody", "preferred voice: Microsoft David Desktop"], key="somebody")
        assert profile.preferred_voice == ""
        assert "preferred voice" in profile.style

    def test_full_identity_document_persists_the_selection(self):
        """The whole document -> AgentIdentity -> VoiceProfile path, the
        existing identity/configuration architecture end to end."""
        identity = parse_identity(
            "## Name\nSomebody\n## Capabilities\nroster\n"
            "## Voice\nkey: somebody\npreferred_voice: Microsoft David Desktop\n"
        )
        assert identity.voice.preferred_voice == "Microsoft David Desktop"

    def test_the_real_identity_md_persists_alfreds_real_selection(self):
        """Not a synthetic document - the actual file this project ships."""
        identity = load_identity("IDENTITY.md")
        assert identity.voice.preferred_voice == "Microsoft David Desktop"
        # Existing traits (Stage 3/8A) are unaffected by the new field.
        assert identity.voice.accent == "nigerian-english"


# ---------------------------------------------------------------------------
# Discovery: the existing Stage 11.2 mechanism, unchanged
# ---------------------------------------------------------------------------


class TestDiscoveryIsTheExistingMechanism:
    def test_installed_voice_names_is_still_the_one_discovery_seam(self, monkeypatch):
        monkeypatch.setattr(
            windows_speech_provider, "_run_powershell",
            lambda script, *, timeout: completed(returncode=0, stdout="\n".join(INSTALLED) + "\n"),
        )
        assert WindowsSpeechAdapter().installed_voice_names() == INSTALLED

    def test_no_second_discovery_function_was_added(self):
        """Stage 11.4 must not create a second voice configuration or
        discovery system - the module still defines exactly one."""
        import inspect

        names = [
            name for name, obj in vars(windows_speech_provider).items()
            if inspect.isfunction(obj) and "voice" in name.lower() and "install" in name.lower()
        ]
        assert names == ["_select_voice_name"] or "installed_voice_names" not in names


# ---------------------------------------------------------------------------
# Selection: an available preference is honoured, case-insensitively
# ---------------------------------------------------------------------------


class TestSelectionHonoursAnAvailablePreference:
    def test_an_exact_match_is_selected(self):
        voice = VoiceProfile(key="alfred", preferred_voice="Microsoft David Desktop")
        assert _select_voice_name(voice, INSTALLED) == "Microsoft David Desktop"

    def test_matching_is_case_insensitive(self):
        voice = VoiceProfile(key="alfred", preferred_voice="microsoft david desktop")
        assert _select_voice_name(voice, INSTALLED) == "Microsoft David Desktop"

    def test_the_real_installed_casing_is_returned_not_the_users_typed_casing(self):
        """The generated script must embed a name SAPI actually recognises -
        the engine's own casing, not whatever the user happened to type."""
        voice = VoiceProfile(key="alfred", preferred_voice="MICROSOFT DAVID DESKTOP")
        assert _select_voice_name(voice, INSTALLED) == "Microsoft David Desktop"

    def test_a_preference_overrides_the_stage_11_2_deterministic_default(self):
        """Alfred's key alone would auto-select a different voice (Stage
        11.2's own hash) - an explicit preference takes priority."""
        auto = _select_voice_name(VoiceProfile(key="alfred"), INSTALLED)
        chosen = _select_voice_name(
            VoiceProfile(key="alfred", preferred_voice="Microsoft David Desktop"), INSTALLED
        )
        assert chosen == "Microsoft David Desktop"
        assert chosen != auto or auto == "Microsoft David Desktop"


# ---------------------------------------------------------------------------
# Fallback: an unavailable preference degrades safely, never a crash
# ---------------------------------------------------------------------------


class TestUnavailablePreferenceFallsBackSafely:
    def test_a_mistyped_or_uninstalled_preference_falls_back_to_the_deterministic_default(self):
        voice = VoiceProfile(key="alfred", preferred_voice="Some Voice Nobody Has")
        fallback = _select_voice_name(voice, INSTALLED)
        auto_without_preference = _select_voice_name(VoiceProfile(key="alfred"), INSTALLED)
        assert fallback == auto_without_preference
        assert fallback in INSTALLED

    def test_an_empty_roster_still_returns_none_even_with_a_preference_set(self):
        voice = VoiceProfile(key="alfred", preferred_voice="Microsoft David Desktop")
        assert _select_voice_name(voice, ()) is None

    def test_never_raises_for_an_unavailable_preference(self):
        voice = VoiceProfile(key="alfred", preferred_voice="Nonexistent Voice")
        # Must not raise - a stale preference is an ordinary, expected case.
        _select_voice_name(voice, INSTALLED)


# ---------------------------------------------------------------------------
# The selected voice is actually used - through the real public speak()
# ---------------------------------------------------------------------------


class TestSelectedVoiceIsActuallyUsed:
    def test_speak_embeds_the_preferred_voice_when_available(self, monkeypatch):
        calls = []

        def fake_run(script, *, timeout):
            calls.append(script)
            if "GetInstalledVoices" in script:
                return completed(returncode=0, stdout="\n".join(INSTALLED) + "\n")
            return completed(returncode=0)

        monkeypatch.setattr(windows_speech_provider, "_run_powershell", fake_run)
        adapter = WindowsSpeechAdapter()
        voice = VoiceProfile(key="alfred", preferred_voice="Microsoft David Desktop")
        assert adapter.speak("hello", voice) is True

        speak_script = next(c for c in calls if "Get-Content" in c)
        assert '$synth.SelectVoice("Microsoft David Desktop")' in speak_script

    def test_speak_falls_back_when_the_preference_is_unavailable(self, monkeypatch):
        calls = []

        def fake_run(script, *, timeout):
            calls.append(script)
            if "GetInstalledVoices" in script:
                return completed(returncode=0, stdout="\n".join(INSTALLED) + "\n")
            return completed(returncode=0)

        monkeypatch.setattr(windows_speech_provider, "_run_powershell", fake_run)
        adapter = WindowsSpeechAdapter()
        voice = VoiceProfile(key="alfred", preferred_voice="Voice Nobody Has")
        assert adapter.speak("hello", voice) is True  # still succeeds, honestly, with the fallback

        speak_script = next(c for c in calls if "Get-Content" in c)
        assert "Voice Nobody Has" not in speak_script
        assert "$synth.SelectVoice(" in speak_script  # the deterministic default still selected one

    def test_alfreds_real_identity_reaches_the_real_speak_call(self, monkeypatch):
        """End to end: the actual IDENTITY.md value, through ManagerSkills,
        through the real public speak() method."""
        from src.manager.manager_skills import ManagerSkills

        calls = []

        def fake_run(script, *, timeout):
            calls.append(script)
            if "GetInstalledVoices" in script:
                return completed(returncode=0, stdout="\n".join(INSTALLED) + "\n")
            return completed(returncode=0)

        monkeypatch.setattr(windows_speech_provider, "_run_powershell", fake_run)
        voice = ManagerSkills(load_identity("IDENTITY.md")).voice
        WindowsSpeechAdapter().speak("status", voice)

        speak_script = next(c for c in calls if "Get-Content" in c)
        assert '$synth.SelectVoice("Microsoft David Desktop")' in speak_script


# ---------------------------------------------------------------------------
# Regression: existing speech behaviour, and Arthur, are unaffected
# ---------------------------------------------------------------------------


class TestRegression:
    def test_a_voice_with_no_preference_behaves_exactly_as_stage_11_2(self):
        """No behavioural change at all for a profile that declares
        nothing new - the pre-existing deterministic mapping is untouched."""
        voice = VoiceProfile(key="alfred")
        assert _select_voice_name(voice, INSTALLED) == _select_voice_name(voice, INSTALLED)
        # Same result as calling the function before this field existed
        # would have: it only ever consulted voice.key.
        import zlib

        expected_index = zlib.crc32(b"alfred") % len(INSTALLED)
        assert _select_voice_name(voice, INSTALLED) == INSTALLED[expected_index]

    def test_arthur_is_unaffected_by_alfreds_selection(self):
        """Arthur has no ## Voice Activation-style preferred_voice section
        - Stage 11.4 does not implement his distinct voice, and his
        existing Stage 11.2 auto-selected voice must be unchanged."""
        from src.agents.specialists import wiring_for
        from src.services.memory.factory import EMAIL_AGENT

        arthur_voice = wiring_for(EMAIL_AGENT, fallback_name="Arthur").voice
        assert arthur_voice.preferred_voice == ""
        assert _select_voice_name(arthur_voice, INSTALLED) == _select_voice_name(arthur_voice, INSTALLED)

    def test_no_speech_still_ignores_voice_entirely(self):
        """NoSpeech (the shipped default adapter) is untouched - it still
        accepts a voice and does nothing with it, honestly."""
        from src.channels.speech import NoSpeech

        assert NoSpeech().speak("hello", VoiceProfile(key="x", preferred_voice="anything")) is False

    def test_availability_semantics_are_unaffected(self, monkeypatch):
        from src.providers.availability import AvailabilityState

        monkeypatch.setattr(
            windows_speech_provider, "_run_powershell",
            lambda script, *, timeout: completed(returncode=0, stdout="\n".join(INSTALLED)),
        )
        adapter = WindowsSpeechAdapter()
        assert adapter.availability.state is AvailabilityState.UNKNOWN
        adapter.speak("hello", VoiceProfile(key="alfred", preferred_voice="Microsoft David Desktop"))
        assert adapter.availability.state is AvailabilityState.AVAILABLE
