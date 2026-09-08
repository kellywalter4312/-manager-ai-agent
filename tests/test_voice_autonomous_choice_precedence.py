"""Agent-autonomous voice selection - the persistence/precedence layer.

Proves ``src/services/identity/voice_preference.py``'s new
``AutonomousVoiceChoice`` machinery directly, against a real, built
Manager and its real Memory (``scripts/control_center.py::build``) -
never a fake store. The ``select_voice`` capability itself (which writes
these records through the governed ``request.retain`` path during an
ordinary conversation turn) is covered separately in
``tests/test_voice_autonomous_selection_capability.py``; this file
proves the storage/precedence rules the capability depends on are
correct in isolation first.
"""

from __future__ import annotations

from src.config.settings import Settings
from src.services.identity.voice_options import VoiceOption
from src.services.identity.voice_preference import (
    autonomous_voice_choice_for,
    build_voice_autonomous_choice_record,
    effective_voice_for,
    set_voice_preference,
)
from scripts.control_center import build


def real_system(**env_overrides):
    env = {"MANAGER_SPEECH": "windows", **env_overrides}
    settings = Settings.from_env(env=env, memory="ephemeral")
    return build(settings)


ITALIAN = VoiceOption("it-voice-1", "Italian Voice One", "elevenlabs", language="it", accent="italian")


class TestAutonomousChoiceStorageIsHonest:
    def test_nothing_stored_reads_back_as_none(self):
        runtime, manager, channel, conversation = real_system()
        assert autonomous_voice_choice_for("Alfred", recall=manager.memory.recall) is None

    def test_a_stored_choice_reads_back_exactly_what_was_written(self):
        runtime, manager, channel, conversation = real_system()
        record = build_voice_autonomous_choice_record(
            "Alfred", ITALIAN, language_satisfied=True, accent_satisfied=True
        )
        manager.memory.remember(record)

        choice = autonomous_voice_choice_for("Alfred", recall=manager.memory.recall)
        assert choice is not None
        assert choice.voice_id == "it-voice-1"
        assert choice.name == "Italian Voice One"
        assert choice.provider == "elevenlabs"
        assert choice.language == "it"
        assert choice.accent == "italian"

    def test_a_caller_that_forgets_the_prior_choice_before_writing_the_next_never_accumulates(self):
        """build_voice_autonomous_choice_record itself only builds - it
        holds no recall/forget, the same purity every other build_*
        helper in this project has (mirrors build_recording/
        build_task_record). Replacing a prior choice is the writer's own
        forget-then-remember sequence - proven here the same way, and
        proven again at the real capability in
        test_voice_autonomous_selection_capability.py, which performs
        exactly this sequence through the governed request.retain path."""
        runtime, manager, channel, conversation = real_system()
        first = build_voice_autonomous_choice_record(
            "Alfred", ITALIAN, language_satisfied=True, accent_satisfied=True
        )
        manager.memory.remember(first)

        for existing in manager.memory.recall(kind="user_context", tags={"voice_autonomous_choice:alfred"}, limit=5):
            manager.memory.forget(existing.id)

        second_option = VoiceOption("es-voice-1", "Spanish Voice", "elevenlabs", language="es")
        second = build_voice_autonomous_choice_record(
            "Alfred", second_option, language_satisfied=True, accent_satisfied=False
        )
        manager.memory.remember(second)

        records = manager.memory.recall(kind="user_context", tags={"voice_autonomous_choice:alfred"}, limit=10)
        assert len(records) == 1
        assert autonomous_voice_choice_for("Alfred", recall=manager.memory.recall).voice_id == "es-voice-1"

    def test_two_agents_own_choices_never_leak_into_each_other(self):
        runtime, manager, channel, conversation = real_system()
        alfred_record = build_voice_autonomous_choice_record(
            "Alfred", ITALIAN, language_satisfied=True, accent_satisfied=True
        )
        manager.memory.remember(alfred_record)
        assert autonomous_voice_choice_for("Arthur", recall=manager.memory.recall) is None
        assert autonomous_voice_choice_for("Alfred", recall=manager.memory.recall) is not None


class TestEffectiveVoicePrecedence:
    """Requirement 4's own governance order: explicit user override always
    wins; an agent's autonomous choice applies only in its absence; the
    bare declared default applies only when neither exists."""

    def test_declared_default_when_nothing_was_ever_chosen(self):
        runtime, manager, channel, conversation = real_system()
        agent = manager.registry.get("Alfred") or manager.own_skills
        profile = effective_voice_for(agent, recall=manager.memory.recall)
        assert profile is not None
        assert profile.preferred_voice == "Microsoft David Desktop"  # Alfred's declared default

    def test_autonomous_choice_applies_when_no_explicit_override_exists(self):
        runtime, manager, channel, conversation = real_system()
        agent = manager.registry.get("Alfred") or manager.own_skills
        record = build_voice_autonomous_choice_record(
            agent.name, VoiceOption("Microsoft Hazel Desktop", "Microsoft Hazel Desktop", "windows"),
            language_satisfied=True, accent_satisfied=False,
        )
        manager.memory.remember(record)

        profile = effective_voice_for(agent, recall=manager.memory.recall)
        assert profile.preferred_voice == "Microsoft Hazel Desktop"

    def test_explicit_override_always_wins_over_an_autonomous_choice(self):
        """Requirement 4 - an explicit user choice must never be silently
        overridden, including by a *later-written* autonomous choice."""
        runtime, manager, channel, conversation = real_system()
        agent = manager.registry.get("Alfred") or manager.own_skills

        assert set_voice_preference(
            agent.name, "Microsoft Zira Desktop",
            recall=manager.memory.recall, remember=manager.memory.remember, forget=manager.memory.forget,
        )
        record = build_voice_autonomous_choice_record(
            agent.name, VoiceOption("Microsoft Hazel Desktop", "Microsoft Hazel Desktop", "windows"),
            language_satisfied=True, accent_satisfied=False,
        )
        manager.memory.remember(record)

        profile = effective_voice_for(agent, recall=manager.memory.recall)
        assert profile.preferred_voice == "Microsoft Zira Desktop"

    def test_an_elevenlabs_autonomous_choice_is_applied_through_style_not_preferred_voice(self):
        """The exact bug this design must not have: an ElevenLabs choice
        must land in style["elevenlabs_voice_id"] (what that provider's
        speak() actually reads), never only in preferred_voice (which
        that provider's speak() never looks at)."""
        runtime, manager, channel, conversation = real_system()
        agent = manager.registry.get("Alfred") or manager.own_skills
        record = build_voice_autonomous_choice_record(
            agent.name, ITALIAN, language_satisfied=True, accent_satisfied=True
        )
        manager.memory.remember(record)

        profile = effective_voice_for(agent, recall=manager.memory.recall)
        assert profile.style.get("elevenlabs_voice_id") == "it-voice-1"

    def test_no_recall_at_all_degrades_to_the_bare_declared_default(self):
        runtime, manager, channel, conversation = real_system()
        agent = manager.registry.get("Alfred") or manager.own_skills
        profile = effective_voice_for(agent, recall=None)
        assert profile is not None
        assert profile.preferred_voice == "Microsoft David Desktop"
