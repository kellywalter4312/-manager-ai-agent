"""Voice governance semantics checkpoint - unconstrained selection,
reselect, and release-autonomy as real state, not UI wording.

Every test goes through the real production path: scripts.control_center.build()
-> manager.handle_task() -> the real select_voice capability -> the real
Windows speech adapter's own live-discovered voices. Distinguishes the
exact governance sequence this checkpoint's own brief specifies:

    "Alfred, choose your voice that best suits you."     -> unconstrained
    "I don't like that. Choose another."                 -> reselect
    "No, use this voice." (via the existing voice command) -> explicit override
    "You can choose your voice again."                    -> release
"""

from __future__ import annotations

from src.config.settings import Settings
from src.control_center.observer import ControlCenter
from src.core.contracts import Task
from src.services.identity.voice_preference import (
    autonomous_voice_choice_for,
    effective_voice_for,
    voice_preference_for,
)
from scripts.control_center import build, run_command


def real_system(**env_overrides):
    env = {"MANAGER_SPEECH": "windows", **env_overrides}
    settings = Settings.from_env(env=env, memory="ephemeral")
    return build(settings)


class TestUnconstrainedVoiceIdentitySelection:
    def test_choose_your_voice_picks_freely_with_no_language_or_accent_implied(self):
        runtime, manager, channel, conversation = real_system()
        result = manager.handle_task(Task(description="Alfred, choose your voice that best suits you."))
        assert result.succeeded, result.error
        assert result.payload["handled_by"] == "select_voice"
        assert result.payload["language_satisfied"] is False
        assert result.payload["accent_satisfied"] is False
        assert result.payload["brain_assisted"] is False

    def test_unconstrained_selection_never_needs_a_brain(self):
        """No LLM configured anywhere in this test - proof the simplest
        request (pure delegation) is the one that least needs reasoning."""
        runtime, manager, channel, conversation = real_system()
        result = manager.handle_task(Task(description="pick whatever voice suits you"))
        assert result.succeeded, result.error


class TestReselectExcludesThePriorChoice:
    def test_choose_another_yields_a_genuinely_different_real_voice(self):
        runtime, manager, channel, conversation = real_system()
        first = manager.handle_task(Task(description="choose your voice"))
        assert first.succeeded
        second = manager.handle_task(Task(description="I don't like that. Choose another."))
        assert second.succeeded
        assert second.payload["voice_id"] != first.payload["voice_id"]

    def test_reselect_honours_the_original_constraint_not_the_prior_pick(self):
        """The prior pick happens to be English (this machine has no
        other language) - reselect must still be asking for "another
        English voice", not silently dropping the language constraint,
        provable because the second choice is still English-satisfied."""
        runtime, manager, channel, conversation = real_system()
        first = manager.handle_task(Task(description="Give me an English voice"))
        assert first.succeeded and first.payload["language_satisfied"]
        second = manager.handle_task(Task(description="not that one, try another"))
        assert second.succeeded
        assert second.payload["language_satisfied"] is True
        assert second.payload["voice_id"] != first.payload["voice_id"]

    def test_reselect_with_no_prior_choice_refuses_honestly_never_fabricates_one(self):
        """A real, observed false-positive risk this design closes: a
        bare 'choose another'/'try another' can belong to any topic, not
        just voice (RESELECT_MARKERS is deliberately not anchored to
        the word 'voice'). Without a prior autonomous choice to move
        away from, fabricating a pick would risk answering a request
        that was never about voice at all - refused honestly instead."""
        runtime, manager, channel, conversation = real_system()
        result = manager.handle_task(Task(description="choose another voice"))
        assert not result.succeeded
        assert "no previous voice choice" in result.error.lower()

    def test_a_reselect_marker_colliding_with_an_unrelated_topic_never_fabricates_a_voice_pick(self):
        runtime, manager, channel, conversation = real_system()
        result = manager.handle_task(Task(description="can you choose another route for the trip"))
        # Whatever claims this (select_voice or something else), it
        # must never come back as a fabricated, successful voice pick.
        if result.payload is not None and result.payload.get("handled_by") == "select_voice":
            assert not result.succeeded

    def test_reselecting_repeatedly_never_gets_stuck_once_options_are_exhausted(self):
        runtime, manager, channel, conversation = real_system()
        manager.handle_task(Task(description="choose your voice"))
        manager.handle_task(Task(description="choose another"))
        # This machine has exactly 3 real Windows voices - a third
        # reselect must still answer honestly (a real third option, or
        # an honest "no other options" - never a crash).
        third = manager.handle_task(Task(description="choose another"))
        assert third.payload is not None or third.error


class TestExplicitOverrideAndRelease:
    def test_explicit_override_then_release_then_autonomous_choice_works_again(self):
        runtime, manager, channel, conversation = real_system()
        centre = ControlCenter(runtime, manager)

        manager.handle_task(Task(description="choose your voice"))
        run_command(centre, "voice Alfred Microsoft Zira Desktop", 100, channel, conversation)
        assert voice_preference_for("Alfred", recall=manager.memory.recall) == "Microsoft Zira Desktop"

        # Deliberately NOT "...again" here - that phrase is itself a
        # release-autonomy marker (see RELEASE_AUTONOMY_MARKERS), which
        # would test something else entirely. This is an ordinary
        # unconstrained attempt while the override is still active.
        deferred = manager.handle_task(Task(description="pick a voice for me"))
        assert deferred.payload.get("deferred_to_override") == "Microsoft Zira Desktop"

        released = manager.handle_task(Task(description="You can choose your voice again."))
        assert released.succeeded
        assert released.payload["released_override"] is True
        assert voice_preference_for("Alfred", recall=manager.memory.recall) is None

        resumed = manager.handle_task(Task(description="choose your voice"))
        assert resumed.succeeded
        assert resumed.payload["handled_by"] == "select_voice"
        assert "deferred_to_override" not in (resumed.payload or {})

    def test_releasing_when_nothing_is_locked_answers_honestly_never_claims_a_change(self):
        runtime, manager, channel, conversation = real_system()
        result = manager.handle_task(Task(description="You can choose your voice again."))
        assert result.succeeded
        assert result.payload["released_override"] is False

    def test_release_never_touches_the_autonomous_choice_record_itself(self):
        runtime, manager, channel, conversation = real_system()
        manager.handle_task(Task(description="choose your voice"))
        before = autonomous_voice_choice_for("Alfred", recall=manager.memory.recall)
        assert before is not None

        run_command(ControlCenter(runtime, manager), "voice Alfred Microsoft Zira Desktop", 100, channel, conversation)
        manager.handle_task(Task(description="you can choose your voice again"))

        after = autonomous_voice_choice_for("Alfred", recall=manager.memory.recall)
        assert after is not None
        assert after.voice_id == before.voice_id


class TestLanguageAccentAndCombinedThroughTheRealCapability:
    """The brief's own four canonical examples, through the real handler."""

    def test_voice_identity_selection_implies_no_language_or_accent(self):
        runtime, manager, channel, conversation = real_system()
        result = manager.handle_task(Task(description="Alfred, choose your voice that best suits you."))
        assert result.payload["language_satisfied"] is False
        assert result.payload["accent_satisfied"] is False

    def test_language_selection_never_implies_an_accent(self):
        runtime, manager, channel, conversation = real_system()
        result = manager.handle_task(Task(description="Alfred, I want you to speak Italian."))
        # Genuinely unsupported on this machine (English-only voices) -
        # refused honestly, never silently satisfied by an accent match.
        assert not result.succeeded
        assert "italian" in result.error.lower()

    def test_accent_selection_never_implies_the_same_named_language(self):
        runtime, manager, channel, conversation = real_system()
        result = manager.handle_task(Task(description="Alfred, I want your voice to have an Italian accent."))
        assert not result.succeeded
        # Refused for lacking the ACCENT specifically - never silently
        # reinterpreted as a language request this machine could satisfy.
        assert "accent" in result.error.lower()

    def test_combined_language_and_accent_are_both_required_when_stated_together(self):
        runtime, manager, channel, conversation = real_system()
        result = manager.handle_task(Task(description="Speak Italian with an Italian accent."))
        assert not result.succeeded
        assert "italian" in result.error.lower()


class TestAlfredAndArthurIsolationForTheseNewActions:
    """The generic capability's own governance state (autonomous choice,
    explicit override, released-or-not) must never cross agents, on the
    identical shared Windows provider connection both use."""

    def test_alfreds_reselect_never_touches_arthurs_voice_state(self):
        runtime, manager, channel, conversation = real_system()
        arthur = manager.registry.get("Arthur")
        arthur_before = effective_voice_for(arthur, recall=arthur.memory.recall)

        manager.handle_task(Task(description="choose your voice"))
        manager.handle_task(Task(description="choose another"))

        arthur_after = effective_voice_for(arthur, recall=arthur.memory.recall)
        assert arthur_after.preferred_voice == arthur_before.preferred_voice
        assert arthur_after.accent == arthur_before.accent

    def test_alfreds_override_and_release_never_touch_arthurs_override_state(self):
        runtime, manager, channel, conversation = real_system()
        centre = ControlCenter(runtime, manager)
        arthur = manager.registry.get("Arthur")

        run_command(centre, "voice Alfred Microsoft Zira Desktop", 100, channel, conversation)
        manager.handle_task(Task(description="You can choose your voice again."))

        assert voice_preference_for("Arthur", recall=arthur.memory.recall) is None
        assert autonomous_voice_choice_for("Arthur", recall=arthur.memory.recall) is None


class TestPersistenceAcrossRestart:
    def test_reselect_state_and_release_survive_a_fresh_build(self, tmp_path):
        settings = Settings.from_env(
            env={"MANAGER_SPEECH": "windows"}, memory="persistent", memory_dir=str(tmp_path / "memory"),
        )
        runtime, manager, channel, conversation = build(settings)
        manager.handle_task(Task(description="choose your voice"))
        second = manager.handle_task(Task(description="choose another"))
        assert second.succeeded
        chosen_id = second.payload["voice_id"]

        runtime2, manager2, channel2, conversation2 = build(settings)
        agent = manager2.registry.get(runtime2.identity.name) or manager2.own_skills
        profile = effective_voice_for(agent, recall=manager2.memory.recall)
        assert profile.preferred_voice == chosen_id

    def test_a_released_override_stays_released_after_a_fresh_build(self, tmp_path):
        settings = Settings.from_env(
            env={"MANAGER_SPEECH": "windows"}, memory="persistent", memory_dir=str(tmp_path / "memory"),
        )
        runtime, manager, channel, conversation = build(settings)
        centre = ControlCenter(runtime, manager)
        run_command(centre, f"voice {runtime.identity.name} Microsoft Zira Desktop", 100, channel, conversation)
        manager.handle_task(Task(description="you can choose your voice again"))

        runtime2, manager2, channel2, conversation2 = build(settings)
        assert voice_preference_for(runtime2.identity.name, recall=manager2.memory.recall) is None


class TestControlCenterReflectsGovernanceStateLiveNeverHardcoded:
    def test_source_line_shows_explicit_override_then_reverts_after_release(self):
        runtime, manager, channel, conversation = real_system()
        centre = ControlCenter(runtime, manager)

        run_command(centre, "voice Alfred Microsoft Zira Desktop", 100, channel, conversation)
        locked_report = run_command(centre, "voice Alfred", 100, channel, conversation)
        assert "source: explicit user override" in locked_report

        manager.handle_task(Task(description="you can choose your voice again"))
        released_report = run_command(centre, "voice Alfred", 100, channel, conversation)
        assert "source: declared default" in released_report

    def test_source_line_shows_autonomous_choice_after_unconstrained_selection(self):
        runtime, manager, channel, conversation = real_system()
        centre = ControlCenter(runtime, manager)
        manager.handle_task(Task(description="choose your voice that best suits you"))
        report = run_command(centre, "voice Alfred", 100, channel, conversation)
        assert "source: agent's own autonomous choice" in report


class TestNoFabricatedOptionsAndNoHardcodedCatalog:
    def test_a_request_for_a_language_no_option_reports_is_never_silently_substituted(self):
        runtime, manager, channel, conversation = real_system()
        result = manager.handle_task(Task(description="Give me a French voice"))
        assert not result.succeeded
        assert "french" in result.error.lower()

    def test_the_language_table_is_a_small_open_dict_not_a_closed_enum(self):
        """Structural proof there is no fixed, finite catalog class -
        LANGUAGE_NAMES is an ordinary, extensible dict."""
        from src.services.identity.voice_options import LANGUAGE_NAMES

        assert isinstance(LANGUAGE_NAMES, dict)
        assert len(LANGUAGE_NAMES) > 10
        # Extensible in place - adding an entry needs no class/schema change.
        LANGUAGE_NAMES["xx"] = "test-language"
        try:
            assert LANGUAGE_NAMES["xx"] == "test-language"
        finally:
            del LANGUAGE_NAMES["xx"]
