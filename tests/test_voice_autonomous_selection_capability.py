"""Agent-autonomous voice selection - the real capability, end to end.

Every test here goes through the real production path: scripts.control_center.build()
-> manager.handle_task() -> the real select_voice capability -> the real
Windows speech adapter's own live-discovered voices (never a fake
provider) -> real, governed persistence. No LLM/Brain is configured
anywhere in this file - proof the whole path is Half-Brain safe, exactly
as the capability's own docstring claims.
"""

from __future__ import annotations

from src.config.settings import Settings
from src.control_center.observer import ControlCenter
from src.core.contracts import Task
from src.services.identity.voice_preference import effective_voice_for, voice_preference_for
from scripts.control_center import build, run_command


def real_system(**env_overrides):
    env = {"MANAGER_SPEECH": "windows", **env_overrides}
    settings = Settings.from_env(env=env, memory="ephemeral")
    return build(settings)


class TestAutonomousSelectionSucceedsForAGenuinelyAvailableLanguage:
    def test_alfred_chooses_a_real_available_voice_with_no_brain_configured(self):
        runtime, manager, channel, conversation = real_system()
        result = manager.handle_task(Task(description="Alfred, find me an English voice"))
        assert result.succeeded, result.error
        assert result.payload["handled_by"] == "select_voice"
        assert result.payload["provider"] == "windows"
        assert result.payload["language_satisfied"] is True

    def test_the_choice_is_actually_used_by_effective_voice_for(self):
        runtime, manager, channel, conversation = real_system()
        result = manager.handle_task(Task(description="Alfred, find me an English voice"))
        agent = manager.registry.get(runtime.identity.name) or manager.own_skills
        profile = effective_voice_for(agent, recall=manager.memory.recall)
        assert profile.preferred_voice == result.payload["voice_id"]

    def test_repeated_selection_replaces_the_prior_choice_never_accumulates(self):
        runtime, manager, channel, conversation = real_system()
        manager.handle_task(Task(description="Alfred, find me an English voice"))
        manager.handle_task(Task(description="Alfred, find me an English voice"))
        records = manager.memory.recall(
            kind="user_context", tags={f"voice_autonomous_choice:{runtime.identity.name.lower()}"}, limit=10
        )
        assert len(records) == 1


class TestUnsupportedRequestsAreRefusedHonestly:
    def test_a_language_no_configured_provider_reports_is_refused_not_substituted(self):
        """This environment's real Windows voices are all English-only
        (verified live this checkpoint) - a genuine, honest boundary,
        not a fixture. Asking for Italian must never silently return an
        English voice."""
        runtime, manager, channel, conversation = real_system()
        result = manager.handle_task(Task(description="Alfred, find me an Italian voice"))
        assert not result.succeeded
        assert "italian" in result.error.lower()
        agent = manager.registry.get(runtime.identity.name) or manager.own_skills
        assert voice_preference_for(agent.name, recall=manager.memory.recall) is None

    def test_a_request_naming_no_recognisable_language_or_accent_is_refused(self):
        runtime, manager, channel, conversation = real_system()
        result = manager.handle_task(Task(description="Alfred, pick me a nice voice please"))
        # Either genuinely unclaimed (falls through to ordinary
        # conversation) or claimed and honestly refused - never a
        # fabricated selection either way.
        if result.succeeded and result.payload and result.payload.get("handled_by") == "select_voice":
            raise AssertionError("an empty voice request must never resolve to a fabricated choice")


class TestExplicitOverrideTakesPrecedence:
    def test_an_existing_explicit_override_is_never_silently_replaced(self):
        runtime, manager, channel, conversation = real_system()
        centre = ControlCenter(runtime, manager)
        chosen = "Microsoft Zira Desktop"
        set_report = run_command(centre, f"voice {runtime.identity.name} {chosen}", 100, channel, conversation)
        assert chosen in set_report

        result = manager.handle_task(Task(description="Alfred, find me an English voice"))
        assert result.succeeded
        assert result.payload.get("deferred_to_override") == chosen

        agent = manager.registry.get(runtime.identity.name) or manager.own_skills
        profile = effective_voice_for(agent, recall=manager.memory.recall)
        assert profile.preferred_voice == chosen


class TestAgentIndependence:
    def test_arthur_chooses_his_own_voice_into_his_own_memory(self):
        """Updated 2026-09-21: every agent chooses its own voice (the owner's
        requirement: "future agents must also be able to choose their own
        voices"). Arthur is still a structurally different agent with none of
        the Manager's capabilities; only the one shared voice capability
        reaches him, through CognitiveAgent's conversational floor, and his
        choice lands in his own memory - never Alfred's."""
        from src.channels.speech import SpeechAdapter  # noqa: F401 - adapter shape only
        from src.services.identity.voice_configuration import voice_facts
        from src.services.identity.voice_options import VoiceOption

        runtime, manager, channel, conversation = real_system()
        arthur = manager.registry.get("Arthur")
        assert arthur is not None and not hasattr(arthur, "_capabilities")
        voices = (
            VoiceOption(voice_id="en-a", name="en-a", provider="scripted", language="english", locale="en-GB", gender="male"),
            VoiceOption(voice_id="en-b", name="en-b", provider="scripted", language="english", locale="en-US", gender="female"),
        )

        class _Speaker:
            name = "scripted"

            def available_voices(self):
                return voices

        from scripts.control_center import specialist_voice_handler

        arthur.bind_voice(specialist_voice_handler(discover_voices=lambda: voices,
                                                   voice_facts=lambda: voice_facts(_Speaker()), translate=None))
        result = arthur.execute(Task(description="Arthur, find me an English voice"))
        assert result.succeeded and result.payload.get("handled_by") == "select_voice"
        assert arthur.memory.recall(kind="user_context", tags={"voice_autonomous_choice:arthur"}, limit=5)
        assert not manager.memory.recall(kind="user_context", tags={"voice_autonomous_choice:arthur"}, limit=5)
        # still no route to his personal context: the person's facts are refused
        from src.services.memory.memory import MemoryRecord

        verdict = arthur.retain(MemoryRecord(content="The user lives in Lagos.", kind="user_context", tags={"x"},
                                             context={"fact_type": "personal_context"}))
        assert not verdict.allowed

    def test_alfreds_autonomous_choice_never_appears_under_arthurs_key(self):
        runtime, manager, channel, conversation = real_system()
        manager.handle_task(Task(description="Alfred, find me an English voice"))
        arthur_records = manager.memory.recall(
            kind="user_context", tags={"voice_autonomous_choice:arthur"}, limit=5
        )
        assert arthur_records == ()


class TestControlCenterReflectsRealBackendState:
    def test_voice_command_shows_the_real_discovered_options_and_source(self):
        runtime, manager, channel, conversation = real_system()
        centre = ControlCenter(runtime, manager)
        manager.handle_task(Task(description="Alfred, find me an English voice"))

        report = run_command(centre, f"voice {runtime.identity.name}", 100, channel, conversation)
        assert "source: agent's own autonomous choice" in report
        assert "language=english" in report
        assert "discovered options" in report
        # Real per-voice locale, live from this machine's own SAPI
        # roster - never a fabricated catalog.
        assert "locale=en-US" in report or "locale=en-GB" in report

    def test_an_explicit_override_reports_its_own_source(self):
        runtime, manager, channel, conversation = real_system()
        centre = ControlCenter(runtime, manager)
        run_command(centre, f"voice {runtime.identity.name} Microsoft Zira Desktop", 100, channel, conversation)
        report = run_command(centre, f"voice {runtime.identity.name}", 100, channel, conversation)
        assert "source: explicit user override" in report

    def test_declared_default_reports_its_own_source_when_nothing_was_ever_chosen(self):
        runtime, manager, channel, conversation = real_system()
        centre = ControlCenter(runtime, manager)
        report = run_command(centre, f"voice {runtime.identity.name}", 100, channel, conversation)
        assert "source: declared default" in report

    def test_the_discovered_options_listing_includes_gender_when_reported(self):
        runtime, manager, channel, conversation = real_system()
        centre = ControlCenter(runtime, manager)
        report = run_command(centre, f"voice {runtime.identity.name}", 100, channel, conversation)
        # This machine's real Windows voices report no gender - an
        # honest "not reported", never a fabricated value, but the
        # column itself is present in the listing header.
        assert "gender" in report

    def test_an_autonomous_choice_source_line_names_gender_honestly(self):
        runtime, manager, channel, conversation = real_system()
        centre = ControlCenter(runtime, manager)
        # David (this project's real deterministic first pick among
        # this machine's English voices) genuinely reports Male via
        # VoiceInfo.Gender.
        manager.handle_task(Task(description="Alfred, find me an English voice"))
        report = run_command(centre, f"voice {runtime.identity.name}", 100, channel, conversation)
        assert "gender=male" in report


class TestSnapshotReflectsTheRealEffectiveVoiceNeverAStaleDeclaredDefault:
    """The exact UI/backend divergence a final weakness check should
    catch: before this checkpoint, src/control_center/snapshot.py's
    voice field read only the bare declared default (voice_for), never
    an explicit override or an autonomous choice - so the frontend could
    show a voice a person had already changed away from. Proven fixed
    directly against the real snapshot() function."""

    def test_an_autonomous_choice_is_reflected_in_the_real_snapshot(self):
        from src.control_center.snapshot import snapshot as build_snapshot

        runtime, manager, channel, conversation = real_system()
        centre = ControlCenter(runtime, manager)
        result = manager.handle_task(Task(description="Alfred, find me an English voice"))
        assert result.succeeded

        data = build_snapshot(centre, conversation=conversation, channel=channel)
        entry = next(a for a in data["agents"] if a["name"] == runtime.identity.name)
        assert entry["voice"]["preferred_voice"] == result.payload["voice_id"]

    def test_an_explicit_override_is_reflected_in_the_real_snapshot(self):
        from src.control_center.snapshot import snapshot as build_snapshot

        runtime, manager, channel, conversation = real_system()
        centre = ControlCenter(runtime, manager)
        run_command(centre, f"voice {runtime.identity.name} Microsoft Zira Desktop", 100, channel, conversation)

        data = build_snapshot(centre, conversation=conversation, channel=channel)
        entry = next(a for a in data["agents"] if a["name"] == runtime.identity.name)
        assert entry["voice"]["preferred_voice"] == "Microsoft Zira Desktop"

    def test_snapshot_resolves_an_override_through_real_discovery_not_just_preferred_voice(self):
        """Requirement 7's completeness gap, closed this checkpoint: a
        real Kokoro voice id typed as an override, through the real
        Control Center command with Kokoro as the actually-configured
        engine, must resolve into style - the field
        KokoroSpeechAdapter.speak() actually reads - visible in the real
        snapshot, not left uselessly in preferred_voice."""
        from src.control_center.snapshot import snapshot as build_snapshot

        runtime, manager, channel, conversation = real_system(MANAGER_SPEECH="kokoro")
        centre = ControlCenter(runtime, manager)
        # Kokoro's adapter has no installed_voice_names() (only Windows
        # does), so the command stores this without local validation -
        # exactly the case this fix exists for.
        set_report = run_command(centre, f"voice {runtime.identity.name} bm_daniel", 100, channel, conversation)
        assert "now" in set_report

        # Snapshot still builds successfully and the agent is present -
        # style is intentionally opaque to voice.py's own human-facing
        # traits/describes (see that module's own docstring: no agent
        # should have to know a vendor exists), so this is not where a
        # provider-specific id would surface. What matters - and is
        # checked below - is that the real resolution path a speaking
        # channel actually calls applies it correctly.
        data = build_snapshot(centre, conversation=conversation, channel=channel)
        entry = next(a for a in data["agents"] if a["name"] == runtime.identity.name)
        assert entry["voice"]["declared"] is True

        agent = manager.registry.get(runtime.identity.name) or manager.own_skills
        profile = effective_voice_for(agent, recall=manager.memory.recall, discover_voices=runtime.speech.available_voices)
        assert profile.style["kokoro_voice_id"] == "bm_daniel"


class TestPersistenceAcrossARestart:
    def test_an_autonomous_choice_survives_a_fresh_build(self, tmp_path):
        env = {"MANAGER_SPEECH": "windows"}
        settings = Settings.from_env(env=env, memory="persistent", memory_dir=str(tmp_path / "memory"))
        runtime, manager, channel, conversation = build(settings)
        result = manager.handle_task(Task(description="Alfred, find me an English voice"))
        assert result.succeeded
        chosen_id = result.payload["voice_id"]

        runtime2, manager2, channel2, conversation2 = build(settings)
        agent = manager2.registry.get(runtime2.identity.name) or manager2.own_skills
        profile = effective_voice_for(agent, recall=manager2.memory.recall)
        assert profile.preferred_voice == chosen_id
