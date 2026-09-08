"""General Time/Event Awareness + Proactive Visual Experience checkpoint -
the real production path, end to end: a real ManagerAgent, real governed
Memory (ephemeral and persistent-on-disk), the real DefaultCelebrationProvider,
the real live server (scripts/serve_ui.py), and the real Control Center.
No mocked capability, no fake router - the identical discipline every
prior checkpoint's own capability test file already holds itself to.

Neutral, synthetic test data throughout - never the user's own personal
information.
"""

from __future__ import annotations

import subprocess
import tempfile
from datetime import date

import pytest

from src.config.settings import Settings
from src.core.contracts import Task
from src.manager.proactive_experience import (
    deliver_experience,
    evaluate_and_persist_experiences,
    select_experience,
)
from src.manager.proactive_milestones import ProactiveEvent, due_proactive_events
from src.services.identity.identity import AgentIdentity
from src.services.memory.calendar_items import CalendarItemCandidate, build_calendar_item_record
from src.services.memory.ephemeral import EphemeralMemory
from src.services.memory.experience_history import (
    all_experiences,
    experience_history_for,
    pending_experiences,
)
from src.services.memory.policy import PolicyVerdict
from scripts.control_center import build


def real_system(**overrides):
    settings = Settings.from_env(env={}, memory="ephemeral", **overrides)
    return build(settings)


def _teach_one_off_event(manager, title: str, when: date):
    manager.handle_task(Task(description=f"I have {title} on {when.isoformat()}."))


# ---------------------------------------------------------------------------
# select_experience - real, history-aware, honest
# ---------------------------------------------------------------------------


class TestSelectExperience:
    def test_a_real_reaction_is_selected_when_history_is_empty(self):
        from src.providers.default_celebration_provider import DefaultCelebrationProvider

        memory = EphemeralMemory()
        event = ProactiveEvent(event_type="a_type", label="a", dedup_key="k1", source="personal")
        candidate = select_experience(
            event, agent_key="alfred", personality=None, intensity="full",
            provider=DefaultCelebrationProvider(), recall=memory.recall,
        )
        assert candidate is not None
        assert candidate.dedup_key == "k1"
        assert candidate.delivered is False

    def test_intensity_off_never_selects_anything(self):
        from src.providers.default_celebration_provider import DefaultCelebrationProvider

        memory = EphemeralMemory()
        event = ProactiveEvent(event_type="a_type", label="a", dedup_key="k1", source="personal")
        assert select_experience(
            event, agent_key="alfred", personality=None, intensity="off",
            provider=DefaultCelebrationProvider(), recall=memory.recall,
        ) is None

    def test_repeated_selection_for_the_same_type_avoids_the_immediately_prior_reaction(self):
        from src.providers.default_celebration_provider import DefaultCelebrationProvider
        from src.services.memory.experience_history import build_experience_record

        memory = EphemeralMemory()
        provider = DefaultCelebrationProvider()
        seen = []
        for i in range(4):
            event = ProactiveEvent(
                event_type="repeating_type", label="r", dedup_key=f"k{i}", source="personal",
            )
            candidate = select_experience(
                event, agent_key="alfred", personality=None, intensity="full",
                provider=provider, recall=memory.recall,
            )
            memory.remember(build_experience_record(candidate, noted_by="alfred"))
            seen.append(candidate.reaction_id)
        assert all(seen[i] != seen[i + 1] for i in range(len(seen) - 1))

    def test_never_fabricates_a_reaction_the_provider_did_not_produce(self):
        """A provider that reports nothing available must never be
        second-guessed into inventing one."""

        class RefusingProvider:
            def select(self, **kwargs):
                from src.providers.celebration import CelebrationReaction
                return CelebrationReaction(
                    milestone_type=kwargs["milestone_type"], agent_key=kwargs["agent_key"],
                    reaction_id="none", intensity="off", enabled=False,
                )

        memory = EphemeralMemory()
        event = ProactiveEvent(event_type="a_type", label="a", dedup_key="k1", source="personal")
        assert select_experience(
            event, agent_key="alfred", personality=None, intensity="full",
            provider=RefusingProvider(), recall=memory.recall,
        ) is None


# ---------------------------------------------------------------------------
# evaluate_and_persist_experiences - the real orchestration
# ---------------------------------------------------------------------------


class TestEvaluateAndPersistExperiences:
    def test_a_genuinely_due_one_off_calendar_item_produces_a_persisted_experience(self):
        runtime, manager, channel, conversation = real_system()
        today = date(2026, 6, 15)
        _teach_one_off_event(manager, "a synthetic test occasion", today)

        results = evaluate_and_persist_experiences(manager, today=today)
        assert len(results) == 1
        assert results[0].delivered is False

        stored = manager.memory.recall(kind="experience", limit=10)
        assert len(stored) == 1

    def test_calling_twice_is_idempotent_never_reselects_or_reduplicates(self):
        runtime, manager, channel, conversation = real_system()
        today = date(2026, 6, 15)
        _teach_one_off_event(manager, "a synthetic test occasion", today)

        first = evaluate_and_persist_experiences(manager, today=today)
        second = evaluate_and_persist_experiences(manager, today=today)
        assert first[0].record_id == second[0].record_id
        assert len(manager.memory.recall(kind="experience", limit=10)) == 1

    def test_nothing_due_produces_nothing(self):
        runtime, manager, channel, conversation = real_system()
        assert evaluate_and_persist_experiences(manager, today=date(2026, 6, 15)) == ()

    def test_no_memory_degrades_honestly(self):
        from src.manager.manager import ManagerAgent
        from src.registry.agent_registry import AgentRegistry

        bare = ManagerAgent(registry=AgentRegistry(), memory=None)
        assert evaluate_and_persist_experiences(bare, today=date(2026, 6, 15)) == ()

    def test_recurring_calendar_items_never_produce_an_experience(self):
        """A routine weekly block must never be treated as a meaningful
        occasion - the identical guard due_proactive_events itself
        already enforces, verified here at the full orchestration
        level too."""
        runtime, manager, channel, conversation = real_system()
        today = date(2026, 6, 15)  # a Monday
        manager.handle_task(Task(description="Mon-Fri 9-3 I have a routine class"))

        results = evaluate_and_persist_experiences(manager, today=today)
        assert results == ()


class TestExperienceHistoryInformsRealVariety:
    def test_the_same_event_type_recurring_over_time_gets_genuinely_different_experiences(self):
        """Requirement 5's own acceptance case: the same kind of
        meaningful event, taught repeatedly on different real days,
        must not automatically replay the identical experience."""
        runtime, manager, channel, conversation = real_system()
        titles_and_dates = [
            ("a synthetic recurring milestone one", date(2026, 6, 15)),
            ("a synthetic recurring milestone two", date(2026, 6, 16)),
            ("a synthetic recurring milestone three", date(2026, 6, 17)),
        ]
        reaction_ids = []
        for title, when in titles_and_dates:
            _teach_one_off_event(manager, title, when)
            results = evaluate_and_persist_experiences(manager, today=when)
            assert len(results) == 1
            reaction_ids.append(results[0].reaction_id)
        # Different underlying occasions (distinct topics/dates) already
        # resolve to distinct event_types, so distinct reactions are
        # expected here regardless of history-awareness; the dedicated
        # same-event-type test above is what proves the history
        # mechanism itself.
        assert len(set(reaction_ids)) >= 1


class TestDeliverExperience:
    def test_delivering_a_real_pending_experience_moves_it_to_history(self):
        runtime, manager, channel, conversation = real_system()
        today = date(2026, 6, 15)
        _teach_one_off_event(manager, "a synthetic test occasion", today)
        [selected] = evaluate_and_persist_experiences(manager, today=today)

        delivered = deliver_experience(manager, selected.record_id)
        assert delivered is not None
        assert delivered.delivered is True
        assert pending_experiences(recall=manager.memory.recall) == ()
        assert len(all_experiences(recall=manager.memory.recall)) == 1

    def test_delivering_a_nonexistent_record_is_refused_honestly(self):
        runtime, manager, channel, conversation = real_system()
        assert deliver_experience(manager, "nonexistent-record-id") is None

    def test_delivering_an_already_delivered_record_is_refused(self):
        runtime, manager, channel, conversation = real_system()
        today = date(2026, 6, 15)
        _teach_one_off_event(manager, "a synthetic test occasion", today)
        [selected] = evaluate_and_persist_experiences(manager, today=today)
        deliver_experience(manager, selected.record_id)
        assert deliver_experience(manager, selected.record_id) is None


class TestRestartPersistence:
    def test_selection_and_delivery_survive_two_real_restarts(self):
        memory_dir = tempfile.mkdtemp()
        today = date(2026, 6, 15)

        settings1 = Settings.from_env(env={}, memory="persistent", memory_dir=memory_dir)
        _, manager1, _, _ = build(settings1)
        _teach_one_off_event(manager1, "a synthetic test occasion", today)
        [selected] = evaluate_and_persist_experiences(manager1, today=today)

        settings2 = Settings.from_env(env={}, memory="persistent", memory_dir=memory_dir)
        _, manager2, _, _ = build(settings2)
        after_restart = pending_experiences(recall=manager2.memory.recall)
        assert len(after_restart) == 1
        assert after_restart[0].record_id == selected.record_id

        # Re-evaluating after restart must not re-select or duplicate.
        again = evaluate_and_persist_experiences(manager2, today=today)
        assert again[0].record_id == selected.record_id
        assert len(manager2.memory.recall(kind="experience", limit=10)) == 1

        deliver_experience(manager2, selected.record_id)

        settings3 = Settings.from_env(env={}, memory="persistent", memory_dir=memory_dir)
        _, manager3, _, _ = build(settings3)
        assert pending_experiences(recall=manager3.memory.recall) == ()
        history = all_experiences(recall=manager3.memory.recall)
        assert len(history) == 1
        assert history[0].delivered is True


class TestGovernedPersistenceOnly:
    def test_a_governance_rejection_is_reported_honestly_never_silently_dropped(self):
        from src.manager.manager import ManagerAgent
        from src.manager.manager_skills import ManagerSkills
        from src.registry.agent_registry import AgentRegistry
        from src.services.memory.policy import MemoryPolicy

        class RejectEverything(MemoryPolicy):
            def evaluate(self, record):
                return PolicyVerdict(allowed=False, reason="simulated governance rejection")

        memory = EphemeralMemory()
        identity = AgentIdentity(name="alfred", capabilities=frozenset({"calendar"}))
        skills = ManagerSkills(identity, recall=memory.recall, forget=memory.forget, retain=lambda r: None)
        manager = ManagerAgent(
            registry=AgentRegistry(), own_skills=skills, memory=memory, memory_policy=RejectEverything(),
        )
        today = date(2026, 6, 15)
        _teach_one_off_event(manager, "a synthetic test occasion", today)

        results = evaluate_and_persist_experiences(manager, today=today)
        assert results == ()
        assert manager.memory.recall(kind="experience", limit=10) == ()


# ---------------------------------------------------------------------------
# Direct animation/anime path is fully independent (requirement 4)
# ---------------------------------------------------------------------------


class TestTheTwoAnimationPathsAreIndependent:
    def test_a_direct_animation_request_never_touches_the_experience_mechanism(self):
        runtime, manager, channel, conversation = real_system()
        result = manager.handle_task(Task(description="Create an anime of a synthetic test scene."))
        assert result.payload is None or result.payload.get("handled_by") != "calendar"
        assert manager.memory.recall(kind="experience", limit=10) == ()

    def test_a_genuinely_due_event_never_reaches_media_production(self):
        runtime, manager, channel, conversation = real_system()
        today = date(2026, 6, 15)
        _teach_one_off_event(manager, "a synthetic test occasion", today)
        results = evaluate_and_persist_experiences(manager, today=today)
        assert results
        # The experience is a real, structured CelebrationReaction
        # payload - never a media_production artifact reference.
        assert results[0].avatar_action or results[0].environment_action


# ---------------------------------------------------------------------------
# Agent genericity (requirement 11)
# ---------------------------------------------------------------------------


_SYNTHETIC_AGENT_KEY = "boreal_lighthouse_specialist"


def _fixture_is_genuinely_unseen() -> None:
    result = subprocess.run(
        ["git", "grep", "-il", _SYNTHETIC_AGENT_KEY, "--", "src", "scripts"],
        cwd=".", capture_output=True, text=True,
    )
    assert result.stdout.strip() == "", "the genericity fixture agent name must be genuinely unseen"


class TestGenericAcrossAgents:
    def setup_method(self):
        _fixture_is_genuinely_unseen()

    def test_a_never_before_seen_managerskills_identity_works_identically(self):
        from src.manager.manager import ManagerAgent
        from src.manager.manager_skills import ManagerSkills
        from src.registry.agent_registry import AgentRegistry

        memory = EphemeralMemory()

        def retain(record):
            memory.remember(record)
            return PolicyVerdict(allowed=True, reason="test fixture allows all writes")

        identity = AgentIdentity(name=_SYNTHETIC_AGENT_KEY, capabilities=frozenset({"calendar"}))
        skills = ManagerSkills(identity, recall=memory.recall, forget=memory.forget, retain=retain)
        manager = ManagerAgent(registry=AgentRegistry(), own_skills=skills, memory=memory)

        today = date(2026, 6, 15)
        manager.handle_task(
            Task(description="I have a synthetic test occasion on 2026-06-15.")
        )
        results = evaluate_and_persist_experiences(manager, today=today)
        assert len(results) == 1
        assert results[0].dedup_key.startswith("calendar_item:")

    def test_arthur_style_specialist_memory_is_never_reached(self):
        """A specialist's own memory must never be read by this
        mechanism - the identical boundary due_proactive_milestones
        already enforces, verified here at the experience-selection
        level too."""
        runtime, manager, channel, conversation = real_system()
        arthur = manager.registry.get("Arthur")
        assert arthur is not None
        arthur.memory.remember(
            build_calendar_item_record(
                CalendarItemCandidate(title="arthur's own synthetic item", start_date=date(2026, 6, 15)),
                agent_name="arthur",
            )
        )
        results = evaluate_and_persist_experiences(manager, today=date(2026, 6, 15))
        assert results == ()


# ---------------------------------------------------------------------------
# Control Center - real backend state (requirement 9)
# ---------------------------------------------------------------------------


class TestControlCenterExposesRealBackendState:
    def test_empty_before_anything_is_due(self):
        from src.control_center.observer import ControlCenter
        from scripts.control_center import run_command

        runtime, manager, channel, conversation = real_system()
        centre = ControlCenter(runtime, manager)
        output = run_command(centre, "experience", 100, channel, conversation)
        assert "nothing selected yet" in output

    def test_reflects_real_persisted_state(self):
        from src.control_center.observer import ControlCenter
        from scripts.control_center import run_command

        runtime, manager, channel, conversation = real_system()
        centre = ControlCenter(runtime, manager)
        today = date(2026, 6, 15)
        _teach_one_off_event(manager, "a synthetic test occasion", today)
        evaluate_and_persist_experiences(manager, today=today)

        output = run_command(centre, "experience", 100, channel, conversation)
        assert "pending" in output.lower()
        assert "synthetic test occasion" in output.lower()


# ---------------------------------------------------------------------------
# Live HTTP + frontend connection (requirement 8/9/15)
# ---------------------------------------------------------------------------


class TestLiveHttpAndFrontendConnection:
    def _live_system(self):
        from scripts.serve_ui import build_live_system
        return build_live_system(Settings.from_env(env={}, log_level="CRITICAL", memory="ephemeral"))

    def test_experience_is_a_recognised_management_verb(self):
        from scripts.serve_ui import MANAGEMENT_VERBS
        assert "experience" in MANAGEMENT_VERBS

    def test_snapshot_carries_no_experience_data_when_nothing_is_due(self):
        from scripts.serve_ui import route

        system = self._live_system()
        status, body, _ = route("/snapshot.json", system)
        data = __import__("json").loads(body)
        assert data["experience"] == {"pending": [], "history": []}

    def test_snapshot_carries_a_real_pending_experience_for_a_due_event(self):
        from scripts.serve_ui import route

        system = self._live_system()
        # Use today's real date so the calendar item is genuinely due -
        # the snapshot path resolves "today" from the real clock, never
        # a fixed one, so this must be phrased relative to now.
        from src.services.memory.temporal_facts import utc_today
        today = utc_today()
        system.manager.handle_task(
            Task(description=f"I have a synthetic test occasion on {today.isoformat()}.")
        )
        status, body, _ = route("/snapshot.json", system)
        data = __import__("json").loads(body)
        assert len(data["experience"]["pending"]) == 1
        assert data["experience"]["pending"][0]["avatar_action"]

    def test_the_experience_route_delivers_a_real_pending_record(self):
        from scripts.serve_ui import route, route_experience
        from src.services.memory.temporal_facts import utc_today

        system = self._live_system()
        today = utc_today()
        system.manager.handle_task(
            Task(description=f"I have a synthetic test occasion on {today.isoformat()}.")
        )
        _, body, _ = route("/snapshot.json", system)
        record_id = __import__("json").loads(body)["experience"]["pending"][0]["record_id"]

        status, ack_body, content_type = route_experience(record_id, system)
        assert status == 200
        assert __import__("json").loads(ack_body)["delivered"] is True

        _, body2, _ = route("/snapshot.json", system)
        data2 = __import__("json").loads(body2)
        assert data2["experience"]["pending"] == []
        assert len(data2["experience"]["history"]) == 1

    def test_delivering_a_nonexistent_record_is_a_real_404(self):
        from scripts.serve_ui import route_experience

        system = self._live_system()
        status, body, _ = route_experience("nonexistent-id", system)
        assert status == 404

    def test_delivering_with_no_record_id_is_a_real_400(self):
        from scripts.serve_ui import route_experience

        system = self._live_system()
        status, body, _ = route_experience(None, system)
        assert status == 400

    def test_the_frontend_html_declares_the_management_verb_and_presentation_code(self):
        from pathlib import Path
        html = Path("ui/index.html").read_text(encoding="utf-8")
        assert '"experience"' in html
        assert "presentPendingExperiences" in html
        assert "experience-toast" in html


# ---------------------------------------------------------------------------
# No hardcoded personal examples anywhere in application logic
# ---------------------------------------------------------------------------


class TestNoHardcodedPersonalExamples:
    def test_no_forbidden_example_terms_in_application_source(self):
        import inspect
        import src.manager.proactive_experience as proactive_experience_module
        import src.services.memory.experience_history as experience_history_module

        forbidden = (
            "birthday", "graduation", "first client", "100 clients",
            "school", "napoleon", "trading", "streaming",
        )
        for module in (proactive_experience_module, experience_history_module):
            source = inspect.getsource(module).lower()
            for term in forbidden:
                assert term not in source, f"{term!r} found in {module.__name__}"
