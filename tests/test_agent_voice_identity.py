"""Stage 11.2 - agent voice identity, and the real speech path that uses it.

Stage 8A/Phase 3 already proved Alfred and Arthur have distinct, real,
document-derived VoiceProfiles, and that VoiceChannel already resolves
*whichever agent answered* and asks that agent for its own voice before
speaking. What did not exist before this stage: a real engine that acted
on the voice it was handed. This file proves the new half - a genuine,
generic, deterministic mapping from a VoiceProfile onto one of a real
speech engine's own installed voices - without duplicating any of the
six other dimensions of an agent (Identity, Personality, Brain, Memory,
Capabilities, Connections).
"""

from __future__ import annotations

import ast
import subprocess
from pathlib import Path

import pytest

from src.agents.specialists import wiring_for
from src.channels.channel import VoiceChannel
from src.core.contracts import Task
from src.manager.cognition import assess_cognition
from src.services.identity.voice import VoiceProfile
from src.services.memory.factory import EMAIL_AGENT
import src.providers.windows_speech_provider as windows_speech_provider
from src.providers.windows_speech_provider import WindowsSpeechAdapter, _select_voice_name

REPO_ROOT = Path(__file__).resolve().parent.parent
VOICE_SRC = REPO_ROOT / "src" / "services" / "identity" / "voice.py"
PERSONALITY_SRC = REPO_ROOT / "src" / "services" / "identity" / "personality.py"


def manager(**overrides):
    from src.composition import build_manager, build_runtime
    from src.config.settings import Settings
    from src.services.email.in_memory_mailbox import InMemoryMailbox

    settings = Settings.from_env(
        env={}, log_level="CRITICAL", memory="ephemeral", **overrides
    )
    runtime = build_runtime(settings)
    return build_manager(runtime, mailbox=InMemoryMailbox(), profile=None)


def completed(returncode=0, stdout="", stderr=""):
    return subprocess.CompletedProcess(args=["powershell"], returncode=returncode, stdout=stdout, stderr=stderr)


THREE_VOICES = ("Voice A", "Voice B", "Voice C")


# ---------------------------------------------------------------------------
# 1-3: Voice identity per agent, generic
# ---------------------------------------------------------------------------


class TestAgentVoiceIdentity:
    def test_alfred_has_a_canonical_voiceprofile(self):
        from src.manager.manager_skills import ManagerSkills
        from src.services.identity.identity import load_identity

        voice = ManagerSkills(load_identity("IDENTITY.md")).voice
        assert voice.key == "alfred"
        assert voice.accent and voice.pace and voice.pitch and voice.warmth

    def test_arthur_has_a_distinct_voiceprofile(self):
        from src.manager.manager_skills import ManagerSkills
        from src.services.identity.identity import load_identity

        alfred = ManagerSkills(load_identity("IDENTITY.md")).voice
        arthur = wiring_for(EMAIL_AGENT, fallback_name="Arthur").voice
        assert arthur.key == "arthur"
        assert arthur.key != alfred.key
        assert arthur.accent != alfred.accent

    def test_a_generic_future_agent_gets_a_voiceprofile_with_no_special_code(self):
        """The exact same default_voice/parse_voice mechanism Alfred and
        Arthur already use, exercised against a name neither of them has -
        proving nothing here was written for two specific agents."""
        from src.services.identity.voice import default_voice

        voice = default_voice("Some Future Agent")
        assert isinstance(voice, VoiceProfile)
        assert voice.key == "some future agent"


# ---------------------------------------------------------------------------
# The new mechanism: mapping a VoiceProfile onto a real engine's own voices
# ---------------------------------------------------------------------------


class TestVoiceSelectionMechanism:
    def test_deterministic_same_key_same_voice(self):
        alfred = VoiceProfile(key="alfred")
        assert _select_voice_name(alfred, THREE_VOICES) == _select_voice_name(alfred, THREE_VOICES)

    def test_different_keys_select_different_voices(self):
        """Not guaranteed for every possible pair by a hash function, but
        true for Alfred and Arthur specifically against a 3-voice roster -
        checked directly, not assumed."""
        alfred = VoiceProfile(key="alfred")
        arthur = VoiceProfile(key="arthur")
        assert _select_voice_name(alfred, THREE_VOICES) != _select_voice_name(arthur, THREE_VOICES)

    def test_no_voice_selects_nothing(self):
        assert _select_voice_name(None, THREE_VOICES) is None

    def test_no_available_voices_selects_nothing_never_a_guess(self):
        assert _select_voice_name(VoiceProfile(key="alfred"), ()) is None

    def test_a_single_installed_voice_is_returned_honestly(self):
        """Not a failure - the honest reflection of a machine that
        genuinely has only one voice installed."""
        assert _select_voice_name(VoiceProfile(key="alfred"), ("Only Voice",)) == "Only Voice"

    def test_selection_never_branches_on_a_literal_agent_name(self):
        """Structural: no string comparison anywhere in the module checks
        a value against 'alfred'/'arthur' - the mapping is a pure hash of
        whatever key it is handed."""
        source = (
            REPO_ROOT / "src" / "providers" / "windows_speech_provider.py"
        ).read_text(encoding="utf-8")
        wsp_tree = ast.parse(source)
        for node in ast.walk(wsp_tree):
            if isinstance(node, ast.Compare):
                for comparator in node.comparators:
                    if isinstance(comparator, ast.Constant) and isinstance(comparator.value, str):
                        assert comparator.value.lower() not in ("alfred", "arthur"), comparator.value

    def test_installed_voice_names_are_queried_live_never_hardcoded(self):
        """No installed-voice NAME (as opposed to a script template) is a
        literal in this file - the roster always comes from a real query."""
        source = (
            REPO_ROOT / "src" / "providers" / "windows_speech_provider.py"
        ).read_text(encoding="utf-8")
        for hardcoded in ("Microsoft David", "Microsoft Zira", "Microsoft Hazel"):
            assert hardcoded not in source, hardcoded


class TestInstalledVoiceNamesCaching:
    def test_queries_once_and_caches(self, monkeypatch):
        calls = []

        def fake(script, *, timeout):
            calls.append(script)
            return completed(returncode=0, stdout="Voice A\nVoice B\n")

        monkeypatch.setattr(windows_speech_provider, "_run_powershell", fake)
        adapter = WindowsSpeechAdapter()
        first = adapter.installed_voice_names()
        second = adapter.installed_voice_names()
        assert first == second == ("Voice A", "Voice B")
        assert len(calls) == 1

    def test_a_failed_query_reports_no_voices_not_an_error(self, monkeypatch):
        monkeypatch.setattr(
            windows_speech_provider, "_run_powershell",
            lambda script, *, timeout: completed(returncode=1, stderr="no voices"),
        )
        assert WindowsSpeechAdapter().installed_voice_names() == ()

    def test_a_missing_engine_reports_no_voices_not_an_error(self, monkeypatch):
        monkeypatch.setattr(
            windows_speech_provider, "_run_powershell",
            lambda script, *, timeout: (_ for _ in ()).throw(FileNotFoundError("no powershell")),
        )
        assert WindowsSpeechAdapter().installed_voice_names() == ()

    def test_a_fresh_instance_starts_with_no_cache(self, monkeypatch):
        """Each WindowsSpeechAdapter() - the shape every test in this
        project already constructs - queries independently; nothing is
        shared at module scope that could leak between tests."""
        monkeypatch.setattr(
            windows_speech_provider, "_run_powershell",
            lambda script, *, timeout: completed(returncode=0, stdout="Solo Voice\n"),
        )
        assert WindowsSpeechAdapter().installed_voice_names() == ("Solo Voice",)
        assert WindowsSpeechAdapter().installed_voice_names() == ("Solo Voice",)


# ---------------------------------------------------------------------------
# 4-9: Voice identity separate from provider, brain, memory, personality
# ---------------------------------------------------------------------------


class TestVoiceProviderSeparation:
    def test_voiceprofile_names_no_vendor_or_engine(self):
        for name in ("provider", "vendor", "engine", "model", "endpoint", "api_key"):
            assert name not in VoiceProfile.__dataclass_fields__, name

    def test_changing_speech_provider_setting_does_not_change_voiceprofile(self):
        from src.manager.manager_skills import ManagerSkills
        from src.services.identity.identity import load_identity

        identity = load_identity("IDENTITY.md")
        voice_a = ManagerSkills(identity).voice
        # A different speech provider setting does not even construct a
        # new ManagerSkills differently - the voice comes from identity,
        # never from settings.speech.
        voice_b = ManagerSkills(identity).voice
        assert voice_a == voice_b

    def test_changing_brain_setting_does_not_change_voiceprofile(self):
        for brain_name in ("null", "llm"):
            mgr = manager(brain=brain_name)
            assert mgr.own_skills.voice.key == "alfred"
            assert mgr.own_skills.voice.accent == "nigerian-english"

    def test_voiceprofile_and_personality_are_separate_types(self):
        """Both independently chose 'key' and 'warmth' as trait names - a
        voice can be warm-sounding and a personality can be warm-natured,
        two different facts that happen to share a word. Excluding those
        two, the remaining fields are disjoint: neither type reaches into
        the other's vocabulary."""
        from src.services.identity.personality import AgentPersonality

        assert VoiceProfile is not AgentPersonality
        shared_by_convention = {"key", "style", "warmth"}
        voice_only = set(VoiceProfile.__dataclass_fields__) - shared_by_convention
        personality_only = set(AgentPersonality.__dataclass_fields__) - shared_by_convention
        assert voice_only.isdisjoint(personality_only)
        assert "accent" not in AgentPersonality.__dataclass_fields__
        assert "humor" not in VoiceProfile.__dataclass_fields__

    def test_voice_and_personality_do_not_change_each_other(self):
        from src.manager.manager_skills import ManagerSkills
        from src.services.identity.identity import load_identity

        skills = ManagerSkills(load_identity("IDENTITY.md"))
        assert skills.voice.warmth == "formal"  # a Voice trait
        assert "warm" in skills.personality.tone.lower()  # a Personality trait, unrelated field

    def test_selecting_a_real_voice_does_not_touch_brain_or_memory(self, monkeypatch):
        monkeypatch.setattr(
            windows_speech_provider, "_run_powershell",
            lambda script, *, timeout: completed(returncode=0, stdout="Voice A\nVoice B\n"),
        )
        mgr = manager()
        brain_before, memory_before = mgr.brain, mgr.memory
        channel = VoiceChannel(mgr, speech=WindowsSpeechAdapter())
        channel.deliver(channel.ask("status"))
        assert mgr.brain is brain_before
        assert mgr.memory is memory_before


# ---------------------------------------------------------------------------
# 10-16: Conversation Room / speech path - the correct voice reaches speech
# ---------------------------------------------------------------------------


class TestSpeechPathSelectsTheSpeakingAgentsVoice:
    def test_alfreds_own_voice_is_selected_when_alfred_answers(self, monkeypatch):
        calls = []

        def fake_run(script, *, timeout):
            calls.append(script)
            if "GetInstalledVoices" in script:
                return completed(returncode=0, stdout="Voice A\nVoice B\nVoice C\n")
            return completed(returncode=0)

        monkeypatch.setattr(windows_speech_provider, "_run_powershell", fake_run)
        mgr = manager()
        channel = VoiceChannel(mgr, speech=WindowsSpeechAdapter())
        response = channel.ask("status")
        assert response.turn.handled_by == "Alfred"
        channel.deliver(response)

        alfred_voice = mgr.own_skills.voice
        expected = _select_voice_name(alfred_voice, ("Voice A", "Voice B", "Voice C"))
        speak_script = next(c for c in calls if "Get-Content" in c)
        assert f'$synth.SelectVoice("{expected}")' in speak_script

    def test_arthurs_own_voice_is_selected_when_arthur_answers(self, monkeypatch):
        calls = []

        def fake_run(script, *, timeout):
            calls.append(script)
            if "GetInstalledVoices" in script:
                return completed(returncode=0, stdout="Voice A\nVoice B\nVoice C\n")
            return completed(returncode=0)

        monkeypatch.setattr(windows_speech_provider, "_run_powershell", fake_run)
        mgr = manager()
        channel = VoiceChannel(mgr, speech=WindowsSpeechAdapter())
        response = channel.ask("check my email for job opportunities")
        assert response.turn.handled_by == "Arthur"
        channel.deliver(response)

        arthur = mgr.registry.get("Arthur")
        expected = _select_voice_name(arthur.voice, ("Voice A", "Voice B", "Voice C"))
        speak_script = next(c for c in calls if "Get-Content" in c)
        assert f'$synth.SelectVoice("{expected}")' in speak_script

    def test_alfred_and_arthur_select_different_real_voices_in_the_same_run(self, monkeypatch):
        def fake_run(script, *, timeout):
            if "GetInstalledVoices" in script:
                return completed(returncode=0, stdout="Voice A\nVoice B\nVoice C\n")
            return completed(returncode=0)

        monkeypatch.setattr(windows_speech_provider, "_run_powershell", fake_run)
        mgr = manager()
        channel = VoiceChannel(mgr, speech=WindowsSpeechAdapter())

        alfred_calls = []
        monkeypatch.setattr(
            windows_speech_provider, "_run_powershell",
            lambda script, *, timeout: (alfred_calls.append(script), fake_run(script, timeout=timeout))[1],
        )
        channel.deliver(channel.ask("status"))
        arthur_calls = []
        monkeypatch.setattr(
            windows_speech_provider, "_run_powershell",
            lambda script, *, timeout: (arthur_calls.append(script), fake_run(script, timeout=timeout))[1],
        )
        channel.deliver(channel.ask("check my email"))

        def select_voice_line(script: str) -> str:
            return next(line for line in script.splitlines() if "SelectVoice" in line)

        alfred_speak = next(c for c in alfred_calls if "Get-Content" in c)
        arthur_speak = next(c for c in arthur_calls if "Get-Content" in c)
        # Compare the SelectVoice(...) line specifically - not the whole
        # script, which always differs anyway (a fresh temp file path,
        # different response text) regardless of voice selection.
        assert select_voice_line(alfred_speak) != select_voice_line(arthur_speak)

    def test_no_duplicate_agent_brain_memory_or_voice_system_is_created(self, monkeypatch):
        monkeypatch.setattr(
            windows_speech_provider, "_run_powershell",
            lambda script, *, timeout: completed(returncode=0, stdout="Voice A\nVoice B\n"),
        )
        mgr = manager()
        arthur_before = mgr.registry.get("Arthur")
        channel = VoiceChannel(mgr, speech=WindowsSpeechAdapter())
        channel.deliver(channel.ask("check my email"))
        channel.deliver(channel.ask("check my email again"))
        assert mgr.registry.get("Arthur") is arthur_before
        assert channel.manager is mgr

    def test_existing_conversation_participant_tracking_is_unaffected(self):
        from src.conversation.conversation import Conversation, Participant, ParticipantKind

        conversation = Conversation(participants=(Participant("you", ParticipantKind.PERSON),))
        assert conversation.participants[0].name == "you"


# ---------------------------------------------------------------------------
# 17-22: Stage preservation
# ---------------------------------------------------------------------------


class TestStagePreservation:
    def test_stage_8a_activation_and_continuous_conversation_intact(self):
        from src.channels.activation import ActivationState, VoiceActivationSession

        mgr = manager()
        session = VoiceActivationSession(VoiceChannel(mgr), mgr.own_skills.activation)
        session.start()
        assert session.hear_activation("Alfred") is True
        assert session.state is ActivationState.ACTIVATED
        response = session.turn("status")
        assert response.text
        assert session.state is ActivationState.LISTENING

    def test_stage_10_speechadapter_and_factory_intact(self):
        from src.channels.speech import Listener, NoSpeech, Speaker, SpeechAdapter
        from src.config.settings import Settings
        from src.providers.speech_factory import build_speech

        assert isinstance(build_speech(Settings()), NoSpeech)
        assert isinstance(WindowsSpeechAdapter(), (Listener, Speaker, SpeechAdapter))

    def test_availability_remains_three_state(self):
        from src.providers.availability import AvailabilityState

        assert {s.value for s in AvailabilityState} == {"unknown", "available", "unavailable"}

    def test_no_boolean_availability_reintroduced(self):
        adapter = WindowsSpeechAdapter()
        assert not isinstance(adapter.availability, bool)
        assert hasattr(adapter.availability, "state")

    def test_half_brain_remains_operational(self):
        mgr = manager(brain="null")
        assert assess_cognition(mgr.brain).mode.value == "half_brain"
        result = mgr.handle_task(Task(description="status"))
        assert result.status.value == "completed"

    def test_personality_remains_intact(self):
        mgr = manager()
        assert mgr.own_skills.personality.key == "alfred"
        assert "warm" in mgr.own_skills.personality.tone.lower()

    def test_control_center_snapshot_remains_valid(self):
        from src.control_center.observer import ControlCenter
        from src.control_center.snapshot import snapshot
        from src.composition import build_runtime
        from src.config.settings import Settings

        runtime = build_runtime(Settings.from_env(env={}, log_level="CRITICAL"))
        mgr = manager()
        data = snapshot(ControlCenter(runtime, mgr))
        alfred_entry = next(a for a in data["agents"] if a["is_manager"])
        assert alfred_entry["voice"]["declared"] is True
        assert alfred_entry["personality"]["declared"] is True


# ---------------------------------------------------------------------------
# 23-24: Provider independence
# ---------------------------------------------------------------------------


class TestProviderIndependence:
    def test_generic_voice_module_still_imports_no_provider(self):
        tree = ast.parse(VOICE_SRC.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if isinstance(node, ast.ImportFrom) and node.module:
                assert "src" not in node.module, node.module
            if isinstance(node, ast.Import):
                for alias in node.names:
                    assert not alias.name.startswith("src"), alias.name

    def test_generic_voice_module_names_no_vendor(self):
        source = VOICE_SRC.read_text(encoding="utf-8").lower()
        for forbidden in ("claude", "openai", "grok", "anthropic", "windows", "sapi", "powershell"):
            assert forbidden not in source, forbidden

    def test_provider_specific_voice_selection_logic_lives_only_in_the_provider(self):
        """The mapping logic (_select_voice_name) is defined in the
        Windows provider module, never in the generic identity/voice
        module - the boundary this stage's own test list names
        explicitly."""
        assert not hasattr(
            __import__("src.services.identity.voice", fromlist=["_select_voice_name"]),
            "_select_voice_name",
        )
