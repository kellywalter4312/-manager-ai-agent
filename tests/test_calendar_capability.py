"""Generalized Personal Organization/Calendar capability - the real
production path, end to end: scripts.control_center.build() ->
manager.handle_task() -> the real calendar capability (and its
integration hook inside personal_context) -> real governed Memory
(ephemeral and persistent-on-disk) -> real retrieval -> a real
(scripted) Brain for the Tier 2 path. No mocked capability, no fake
router - the identical discipline test_personal_context_capability.py
already holds itself to.
"""

from __future__ import annotations

import subprocess
import tempfile
from datetime import date, time

import pytest

from src.config.settings import Settings
from src.core.contracts import Task, TaskResult
from src.manager.capabilities import CapabilityRequest, calendar
from src.manager.knowledge import gather_knowledge_evidence
from src.manager.llm_brain import LLMManagerBrain
from src.manager.manager_skills import ManagerSkills
from src.providers.base import Completion, LLMProvider
from src.services.identity.identity import AgentIdentity
from src.services.memory.calendar_items import all_current_calendar_items
from src.services.memory.day import Calendar
from src.services.memory.ephemeral import EphemeralMemory
from src.services.memory.policy import PolicyVerdict
from scripts.control_center import build


def real_system(**overrides):
    settings = Settings.from_env(env={}, memory="ephemeral", **overrides)
    return build(settings)


class ScriptedProvider(LLMProvider):
    def __init__(self, answer: str):
        self._answer = answer
        self.calls: list[str] = []

    @property
    def name(self) -> str:
        return "scripted"

    def complete(self, prompt, *, system=None):
        self.calls.append(prompt)
        return Completion(text=self._answer, provider="scripted")


class TestAddRoutesThroughTheRealManagerAndPersists:
    def test_a_dated_appointment_is_added_and_persisted(self):
        runtime, manager, channel, conversation = real_system()
        result = manager.handle_task(Task(description="I have a dentist appointment on 2026-09-11 at 3pm"))
        assert result.succeeded, result.error
        assert result.payload["handled_by"] == "calendar"
        assert result.payload["action"] == "add"
        assert result.payload["brain_assisted"] is False

        stored = manager.memory.recall(kind="calendar_item", limit=10)
        assert len(stored) == 1
        assert stored[0].context["title"] == "dentist appointment"

    def test_a_recurring_weekday_range_is_added(self):
        runtime, manager, channel, conversation = real_system()
        result = manager.handle_task(Task(description="Mon-Fri 9-3 I have IT"))
        assert result.succeeded, result.error
        current = all_current_calendar_items(recall=manager.memory.recall)
        assert len(current) == 1
        assert current[0].recurrence.days_of_week == (0, 1, 2, 3, 4)

    def test_task_creation_is_never_misrouted_to_calendar(self):
        runtime, manager, channel, conversation = real_system()
        result = manager.handle_task(Task(description="add a task to buy milk on Friday"))
        assert result.succeeded, result.error
        assert result.payload["handled_by"] == "create_task"

    def test_a_relational_birthday_is_never_misrouted_to_calendar(self):
        runtime, manager, channel, conversation = real_system()
        result = manager.handle_task(Task(description="my mom's birthday is March 24"))
        assert result.succeeded, result.error
        assert result.payload["handled_by"] == "personal_recurring_events"

    def test_ordinary_conversation_is_never_misclaimed(self):
        runtime, manager, channel, conversation = real_system()
        for text in ("thanks, sounds good", "what do you think?", "I'm tired today"):
            result = manager.handle_task(Task(description=text))
            handled = result.payload.get("handled_by") if result.payload else None
            assert handled != "calendar", text


class TestPersonalContextIntegrationChain:
    """Requirement 5's own chain: user-provided info -> user-context
    understanding -> organizational/calendar interpretation -> structured
    calendar state - proved through the real personal_context capability,
    never a second, disconnected calendar-only path."""

    def test_a_schedule_teaching_statement_creates_both_records(self):
        runtime, manager, channel, conversation = real_system()
        result = manager.handle_task(Task(description="This is my new IT schedule: Mon-Fri 9am-3pm"))
        assert result.succeeded, result.error
        assert result.payload["handled_by"] == "personal_context"
        assert result.payload["calendar_linked"] is True

        personal_context_records = manager.memory.recall(kind="personal_context", limit=10)
        calendar_records = manager.memory.recall(kind="calendar_item", limit=10)
        assert len(personal_context_records) == 1
        assert len(calendar_records) == 1
        assert calendar_records[0].context["title"] == "IT schedule"
        assert calendar_records[0].context["related_personal_context_topic"] == "IT schedule"

    def test_a_non_schedule_personal_fact_creates_no_calendar_state(self):
        runtime, manager, channel, conversation = real_system()
        result = manager.handle_task(Task(description="I've learned Python."))
        assert result.succeeded, result.error
        assert result.payload["handled_by"] == "personal_context"
        assert result.payload["calendar_linked"] is False
        assert manager.memory.recall(kind="calendar_item", limit=10) == ()

    def test_replacing_a_taught_schedule_supersedes_the_calendar_series_too(self):
        runtime, manager, channel, conversation = real_system()
        manager.handle_task(Task(description="This is my IT schedule: Mon-Fri 9am-3pm"))
        manager.handle_task(Task(description="This is my new IT schedule: Tue-Thu 10am-4pm"))

        current = all_current_calendar_items(recall=manager.memory.recall)
        matching = [c for c in current if "schedule" in c.topic_key]
        assert len(matching) == 1
        assert matching[0].start_time == time(10, 0)
        assert matching[0].recurrence.days_of_week == (1, 2, 3)

        history = manager.memory.recall(kind="calendar_item", limit=10)
        assert len(history) == 2, "the earlier calendar series must be preserved, not overwritten"


class TestCancelReschedule:
    def test_cancel_a_specific_item(self):
        runtime, manager, channel, conversation = real_system()
        manager.handle_task(Task(description="I have a dentist appointment on 2026-09-11 at 3pm"))
        result = manager.handle_task(Task(description="cancel my dentist appointment"))
        assert result.succeeded, result.error
        assert result.payload["action"] == "cancel"

        current = all_current_calendar_items(recall=manager.memory.recall)
        assert current == ()
        history = manager.memory.recall(kind="calendar_item", limit=10)
        assert len(history) == 2

    def test_cancelling_something_that_does_not_exist_is_refused_honestly(self):
        runtime, manager, channel, conversation = real_system()
        result = manager.handle_task(Task(description="cancel my nonexistent thing"))
        assert not result.succeeded

    def test_reschedule_updates_the_current_state_without_deleting_history(self):
        runtime, manager, channel, conversation = real_system()
        manager.handle_task(Task(description="I have a dentist appointment on 2026-09-11 at 3pm"))
        result = manager.handle_task(Task(description="move my dentist appointment to 2026-09-12 at 4pm"))
        assert result.succeeded, result.error

        current = all_current_calendar_items(recall=manager.memory.recall)
        assert len(current) == 1
        assert current[0].start_date == date(2026, 9, 12)
        assert current[0].start_time == time(16, 0)

        history = manager.memory.recall(kind="calendar_item", limit=10)
        assert len(history) == 2

    def test_reschedule_of_something_ambiguous_is_refused_not_guessed(self):
        runtime, manager, channel, conversation = real_system()
        manager.handle_task(Task(description="I have a dentist appointment on 2026-09-11 at 3pm"))
        manager.handle_task(Task(description="I have a dentist checkup on 2026-09-12 at 4pm"))
        result = manager.handle_task(Task(description="move my dentist to 5pm"))
        assert not result.succeeded


class TestRetrieval:
    def test_bare_listing_is_empty_before_anything_taught(self):
        runtime, manager, channel, conversation = real_system()
        result = manager.handle_task(Task(description="what's on my calendar"))
        assert result.succeeded
        assert "nothing" in result.output.lower()

    def test_listing_reflects_real_added_items(self):
        # Pre-Stage-9 Campaign 6 (TIM-06): the listing looks ahead from the
        # real today, so the date is computed from it - a fixed date stops
        # being "upcoming" the day after it passes.
        from datetime import timedelta

        from src.services.memory.day import Calendar

        runtime, manager, channel, conversation = real_system()
        ahead = (Calendar.configured(runtime.settings.timezone).today() + timedelta(days=2)).isoformat()
        manager.handle_task(Task(description=f"I have a dentist appointment on {ahead} at 3pm"))
        result = manager.handle_task(Task(description="what's on my calendar"))
        assert result.succeeded
        assert "dentist appointment" in result.output


class TestRestartPersistence:
    def test_add_persist_restart_retrieve_update_persist_restart(self):
        memory_dir = tempfile.mkdtemp()

        settings1 = Settings.from_env(env={}, memory="persistent", memory_dir=memory_dir)
        _, manager1, _, _ = build(settings1)
        r1 = manager1.handle_task(Task(description="I have a dentist appointment on 2026-09-11 at 3pm"))
        r2 = manager1.handle_task(Task(description="Mon-Fri 9-3 I have IT"))
        assert r1.succeeded and r2.succeeded

        settings2 = Settings.from_env(env={}, memory="persistent", memory_dir=memory_dir)
        _, manager2, _, _ = build(settings2)
        after_restart = {c.title: c for c in all_current_calendar_items(recall=manager2.memory.recall)}
        assert "dentist appointment" in after_restart
        assert "IT" in after_restart

        r3 = manager2.handle_task(Task(description="move my dentist appointment to 2026-09-12 at 4pm"))
        assert r3.succeeded

        settings3 = Settings.from_env(env={}, memory="persistent", memory_dir=memory_dir)
        _, manager3, _, _ = build(settings3)
        final = {c.title: c for c in all_current_calendar_items(recall=manager3.memory.recall)}
        assert final["dentist appointment"].start_date == date(2026, 9, 12)
        assert final["dentist appointment"].start_time == time(16, 0)
        assert final["IT"].recurrence.days_of_week == (0, 1, 2, 3, 4)


class TestGovernedPersistenceOnly:
    def test_no_retain_bound_refuses_honestly_never_crashes(self):
        memory = EphemeralMemory()
        request = CapabilityRequest(
            task=Task(description="I have a dentist appointment on 2026-09-11 at 3pm"),
            identity=AgentIdentity(name="alfred", capabilities=frozenset({"calendar"})),
            roster=lambda: (), own_name="alfred", own_capabilities=("calendar",),
            recall=memory.recall, forget=memory.forget, retain=None,
        )
        result = calendar(request)
        assert not result.succeeded
        assert "way to update your calendar" in result.error.lower()

    def test_a_governance_rejection_is_reported_honestly_never_silently_dropped(self):
        memory = EphemeralMemory()

        def reject(record):
            return PolicyVerdict(allowed=False, reason="simulated governance rejection")

        request = CapabilityRequest(
            task=Task(description="I have a dentist appointment on 2026-09-11 at 3pm"),
            identity=AgentIdentity(name="alfred", capabilities=frozenset({"calendar"})),
            roster=lambda: (), own_name="alfred", own_capabilities=("calendar",),
            recall=memory.recall, forget=memory.forget, retain=reject,
        )
        result = calendar(request)
        assert not result.succeeded
        assert "couldn't add" in result.error.lower()
        assert memory.recall(kind="calendar_item", limit=10) == ()


class TestTier2BrainAssistedFallback:
    def test_an_unparseable_calendar_statement_reaches_the_brain_and_is_recorded(self):
        runtime, manager, channel, conversation = real_system()
        provider = ScriptedProvider(
            "TITLE: guitar lesson\nDATE: 2026-09-15\nWEEKDAYS: NONE\n"
            "START_TIME: 5pm\nEND_TIME: NONE"
        )
        manager.brain = LLMManagerBrain(provider, personality=runtime.identity.personality)
        manager.own_skills._converse = manager.brain.converse

        result = manager.handle_task(Task(description="put my guitar lesson on my calendar, it moves around a bit each week"))
        assert result.succeeded, result.error
        assert result.payload["handled_by"] == "calendar"
        assert result.payload["brain_assisted"] is True
        assert result.payload["title"] == "guitar lesson"
        assert provider.calls

        current = all_current_calendar_items(recall=manager.memory.recall)
        assert any(c.title == "guitar lesson" and c.start_date == date(2026, 9, 15) for c in current)

    def test_a_brain_reply_naming_no_title_is_refused_never_guessed(self):
        runtime, manager, channel, conversation = real_system()
        provider = ScriptedProvider("TITLE: NONE")
        manager.brain = LLMManagerBrain(provider, personality=runtime.identity.personality)
        manager.own_skills._converse = manager.brain.converse

        result = manager.handle_task(Task(description="put my guitar lesson on my calendar, it moves around a bit each week"))
        assert not result.succeeded
        assert manager.memory.recall(kind="calendar_item", limit=10) == ()

    def test_no_brain_configured_degrades_to_an_honest_refusal_never_a_crash(self):
        runtime, manager, channel, conversation = real_system()
        result = manager.handle_task(Task(description="put my guitar lesson on my calendar, it moves around a bit each week"))
        assert not result.succeeded
        assert manager.memory.recall(kind="calendar_item", limit=10) == ()


class TestRetrievalIntegrationWithKnowledgeEvidence:
    def test_no_calendar_evidence_is_added_yet_when_nothing_was_taught(self):
        runtime, manager, channel, conversation = real_system()
        evidence = gather_knowledge_evidence("tell me about myself", recall=manager.memory.recall)
        assert not any("calendar" in n.lower() for n in evidence.notes)


_SYNTHETIC_AGENT_KEY = "aurora_wavelength_specialist"


def _fixture_is_genuinely_unseen() -> None:
    result = subprocess.run(
        ["git", "grep", "-il", _SYNTHETIC_AGENT_KEY, "--", "src", "scripts"],
        cwd=".", capture_output=True, text=True,
    )
    assert result.stdout.strip() == "", "the genericity fixture agent name must be genuinely unseen"


class TestCalendarGenericityAcrossAnyAgent:
    """The same proof test_voice_capability_genericity.py and
    test_personal_context_capability.py already hold their own
    capabilities to: a fresh, generic ManagerSkills agent with a
    synthetic name reaches calendar through real dispatch, with zero
    Alfred-specific branching required."""

    def setup_method(self):
        _fixture_is_genuinely_unseen()

    def test_a_brand_new_agent_adds_and_recalls_its_own_calendar_item(self):
        memory = EphemeralMemory()

        def retain(record):
            memory.remember(record)
            return PolicyVerdict(allowed=True, reason="test fixture allows all writes")

        identity = AgentIdentity(name=_SYNTHETIC_AGENT_KEY, capabilities=frozenset({"calendar"}))
        agent = ManagerSkills(identity, recall=memory.recall, forget=memory.forget, retain=retain)

        result = agent.execute(Task(description="I have a dentist appointment on 2026-09-11 at 3pm"))
        assert isinstance(result, TaskResult)
        assert result.succeeded, result.error
        assert result.payload["handled_by"] == "calendar"

        current = all_current_calendar_items(recall=memory.recall)
        assert any(c.title == "dentist appointment" for c in current)


class TestAgentBoundariesArthurNeverGetsThisCapability:
    def test_email_agent_is_not_a_managerskills_instance(self):
        from src.agents.email_agent import EmailAgent
        assert not issubclass(EmailAgent, ManagerSkills)

    def test_calendar_is_registered_only_under_manager_capabilities(self):
        from src.manager.capabilities import MANAGER_CAPABILITIES
        assert "calendar" in MANAGER_CAPABILITIES


class TestControlCenterExposesRealBackendState:
    def test_the_terminal_command_is_empty_before_anything_was_taught(self):
        from src.control_center.observer import ControlCenter
        from scripts.control_center import run_command

        runtime, manager, channel, conversation = real_system()
        centre = ControlCenter(runtime, manager)
        output = run_command(centre, "calendar", 100, channel, conversation)
        assert "nothing" in output.lower()

    def test_the_terminal_command_reflects_real_persisted_state(self):
        from src.control_center.observer import ControlCenter
        from scripts.control_center import run_command

        runtime, manager, channel, conversation = real_system()
        centre = ControlCenter(runtime, manager)
        manager.handle_task(Task(description="I have a dentist appointment on 2026-09-11 at 3pm"))

        output = run_command(centre, "calendar", 100, channel, conversation)
        assert "dentist appointment" in output


class TestLiveHttpAndFrontendConnection:
    def _live_system(self):
        from scripts.serve_ui import build_live_system
        return build_live_system(Settings.from_env(env={}, log_level="CRITICAL", memory="ephemeral"))

    def test_calendar_is_a_recognised_management_verb(self):
        from scripts.serve_ui import MANAGEMENT_VERBS
        assert "calendar" in MANAGEMENT_VERBS

    def test_the_command_route_reaches_the_real_capability_and_backend_state(self):
        from scripts.serve_ui import route_command

        system = self._live_system()
        system.manager.handle_task(Task(description="I have a dentist appointment on 2026-09-11 at 3pm"))

        status, body, content_type = route_command("calendar", None, system)
        assert status == 200
        assert content_type.startswith("text/plain")
        assert "dentist appointment" in body.decode("utf-8")

    def test_the_snapshot_endpoint_carries_the_same_real_state_the_frontend_reads(self):
        from scripts.serve_ui import route

        system = self._live_system()
        system.manager.handle_task(Task(description="I have a dentist appointment on 2026-09-11 at 3pm"))

        status, body, content_type = route("/snapshot.json", system)
        assert status == 200
        data = __import__("json").loads(body)
        assert "calendar_items" in data
        titles = {item["title"] for item in data["calendar_items"]}
        assert "dentist appointment" in titles

    def test_the_snapshot_endpoint_carries_no_data_when_nothing_was_taught(self):
        from scripts.serve_ui import route

        system = self._live_system()
        status, body, _ = route("/snapshot.json", system)
        data = __import__("json").loads(body)
        assert data["calendar_items"] == []

    def test_the_frontend_html_declares_the_management_verb_it_can_actually_call(self):
        from pathlib import Path
        html = Path("ui/index.html").read_text(encoding="utf-8")
        assert '"calendar"' in html
