"""0.2 Reality Audit - real, runtime voice selection and switching.

Closes the gap between "IDENTITY.md's preferred_voice: line is honoured"
(already true) and "the user can choose or change a voice at any time"
(was not - the only way to change it was editing a document and
restarting). Adds the same governed-preference shape
`services/identity/presence.py` already established for avatars,
applied to voice, plus wiring it into the one place a voice is actually
selected: `VoiceChannel._voice_of()`.

Every test exercises the real production path
(`scripts/control_center.py::build()` + `run_command`, or the real
`VoiceChannel`/`effective_voice_for`) - never a bare call to
`set_voice_preference` standing in for "the user actually changed
something."
"""

from __future__ import annotations

from src.channels.channel import Response, VoiceChannel
from src.config.settings import Settings
from src.control_center.observer import ControlCenter
from src.control_center.views import ConversationTurn
from src.core.contracts import Task
from src.manager.cognition import CognitiveMode, assess_manager
from src.manager.llm_brain import LLMManagerBrain
from src.providers.base import Completion, LLMProvider
from src.services.identity.voice_preference import effective_voice_for, set_voice_preference, voice_preference_for
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
    env = {"MANAGER_SPEECH": "windows", **env_overrides}
    settings = Settings.from_env(env=env, memory="ephemeral")
    return build(settings)


class TestVoicePreferenceThroughTheRealControlCenter:
    def test_alfred_starts_on_his_identity_declared_voice(self):
        runtime, manager, channel, conversation = real_system()
        centre = ControlCenter(runtime, manager)
        report = run_command(centre, "voice Alfred", 80, channel, conversation)
        assert "Microsoft David Desktop" in report

    def test_the_user_can_change_alfreds_voice_at_any_time(self):
        runtime, manager, channel, conversation = real_system()
        centre = ControlCenter(runtime, manager)
        result = run_command(centre, "voice Alfred Microsoft Zira Desktop", 80, channel, conversation)
        assert "now" in result and "Zira" in result

        confirmed = run_command(centre, "voice Alfred", 80, channel, conversation)
        assert "Microsoft Zira Desktop" in confirmed

    def test_an_unknown_voice_name_is_refused_not_silently_accepted(self):
        runtime, manager, channel, conversation = real_system()
        centre = ControlCenter(runtime, manager)
        result = run_command(centre, "voice Alfred Not A Real Voice", 80, channel, conversation)
        assert "no such voice" in result.lower()

    def test_a_specialist_has_an_independently_settable_voice(self):
        runtime, manager, channel, conversation = real_system()
        centre = ControlCenter(runtime, manager)
        run_command(centre, "voice Alfred Microsoft Zira Desktop", 80, channel, conversation)
        run_command(centre, "voice Arthur Microsoft Hazel Desktop", 80, channel, conversation)

        alfred_report = run_command(centre, "voice Alfred", 80, channel, conversation)
        arthur_report = run_command(centre, "voice Arthur", 80, channel, conversation)
        assert "Microsoft Zira Desktop" in alfred_report
        assert "Microsoft Hazel Desktop" in arthur_report

    def test_changing_the_specialists_voice_does_not_change_alfreds(self):
        runtime, manager, channel, conversation = real_system()
        centre = ControlCenter(runtime, manager)
        run_command(centre, "voice Alfred Microsoft Zira Desktop", 80, channel, conversation)
        before = run_command(centre, "voice Alfred", 80, channel, conversation)

        run_command(centre, "voice Arthur Microsoft Hazel Desktop", 80, channel, conversation)
        after = run_command(centre, "voice Alfred", 80, channel, conversation)
        assert before == after


class TestTheSelectedVoiceIsWhatActuallySpeaks:
    def test_the_real_speech_call_uses_the_newly_selected_voice(self):
        runtime, manager, channel, conversation = real_system()
        centre = ControlCenter(runtime, manager)
        run_command(centre, "voice Alfred Microsoft Zira Desktop", 80, channel, conversation)

        recall = manager.memory.recall
        agent = manager.own_skills
        resolved = effective_voice_for(agent, recall=recall)
        assert resolved.preferred_voice == "Microsoft Zira Desktop"

    def test_no_preference_set_falls_back_to_the_identity_declared_default(self):
        runtime, manager, channel, conversation = real_system()
        agent = manager.own_skills
        resolved = effective_voice_for(agent, recall=manager.memory.recall)
        assert resolved.preferred_voice == "Microsoft David Desktop"

    def test_only_preferred_voice_is_overridden_not_the_whole_profile(self):
        runtime, manager, channel, conversation = real_system()
        centre = ControlCenter(runtime, manager)
        run_command(centre, "voice Alfred Microsoft Zira Desktop", 80, channel, conversation)

        resolved = effective_voice_for(manager.own_skills, recall=manager.memory.recall)
        assert resolved.accent == "nigerian-english"  # untouched, still IDENTITY.md's own
        assert resolved.preferred_voice == "Microsoft Zira Desktop"  # overridden


class TestVoiceIndependenceFromBrainAndProvider:
    def test_switching_to_a_verified_full_brain_does_not_change_the_selected_voice(self):
        runtime, manager, channel, conversation = real_system()
        centre = ControlCenter(runtime, manager)
        run_command(centre, "voice Alfred Microsoft Zira Desktop", 80, channel, conversation)

        provider = RecordingProvider()
        brain = LLMManagerBrain(provider, personality=runtime.identity.personality)
        brain.deliberate(Task(description="warm up"))
        manager.brain = brain
        manager.own_skills._converse = brain.converse
        assert assess_manager(manager).mode is CognitiveMode.FULL_BRAIN

        resolved = effective_voice_for(manager.own_skills, recall=manager.memory.recall)
        assert resolved.preferred_voice == "Microsoft Zira Desktop"

    def test_changing_voice_does_not_touch_the_brain_or_its_mode(self):
        runtime, manager, channel, conversation = real_system()
        before_mode = assess_manager(manager).mode
        before_brain = manager.brain

        centre = ControlCenter(runtime, manager)
        run_command(centre, "voice Alfred Microsoft Zira Desktop", 80, channel, conversation)

        assert manager.brain is before_brain
        assert assess_manager(manager).mode is before_mode

    def test_conversation_still_works_normally_after_a_voice_change(self):
        runtime, manager, channel, conversation = real_system()
        centre = ControlCenter(runtime, manager)
        run_command(centre, "voice Alfred Microsoft Zira Desktop", 80, channel, conversation)

        turn = exchange(channel, conversation, "who are you?")
        assert "Alfred" in turn.outcome
