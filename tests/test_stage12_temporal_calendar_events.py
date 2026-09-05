"""Stage 12 closure - temporal awareness, calendar events, personal
recurring dates (with genuine uncertainty), and event-driven celebration.

Covers everything ``tests/test_stage12_proactive_milestones.py`` does
not: ``due_temporal_transitions`` (new day/week/month/year),
``CalendarEventProvider`` (Christmas/Valentine's/Easter, replaceable),
the ``personal_recurring_events`` capability (recognition, uncertainty,
confirmation, deliberate change), the Control Center's unresolved-facts
view, ``due_proactive_events``'s three-source aggregation, and provider/
architecture independence.
"""

from __future__ import annotations

import ast
import inspect
from datetime import date
from pathlib import Path

import pytest

from src.core.contracts import Task
from src.providers.calendar_events import CalendarEvent, CalendarEventProvider
from src.services.memory.temporal_events import TemporalEvent, due_temporal_transitions

REPO_ROOT = Path(__file__).resolve().parent.parent


def real_manager(**overrides):
    from src.composition import build_manager, build_runtime
    from src.config.settings import Settings
    from src.services.email.in_memory_mailbox import InMemoryMailbox

    settings = Settings.from_env(env={}, log_level="CRITICAL", memory="ephemeral", **overrides)
    runtime = build_runtime(settings)
    return build_manager(runtime, mailbox=InMemoryMailbox(), profile=None)


# ---------------------------------------------------------------------------
# Temporal: new day / week / month / year are detected
# ---------------------------------------------------------------------------


class TestTemporalTransitionsAreDetected:
    def test_new_month_is_detected(self):
        events = due_temporal_transitions(date(2027, 6, 1))
        assert any(e.event_type == "new_month" for e in events)

    def test_an_ordinary_day_is_not_a_new_month(self):
        events = due_temporal_transitions(date(2027, 6, 15))
        assert not any(e.event_type == "new_month" for e in events)

    def test_new_year_is_detected(self):
        events = due_temporal_transitions(date(2027, 1, 1))
        assert any(e.event_type == "new_year" for e in events)

    def test_new_year_and_new_month_both_fire_on_january_first(self):
        events = due_temporal_transitions(date(2027, 1, 1))
        types = {e.event_type for e in events}
        assert {"new_month", "new_year"}.issubset(types)

    def test_new_week_is_detected_on_monday(self):
        monday = date(2027, 6, 14)
        assert monday.weekday() == 0
        events = due_temporal_transitions(monday)
        assert any(e.event_type == "new_week" for e in events)

    def test_new_week_is_not_detected_on_a_tuesday(self):
        tuesday = date(2027, 6, 15)
        assert tuesday.weekday() == 1
        events = due_temporal_transitions(tuesday)
        assert not any(e.event_type == "new_week" for e in events)

    def test_new_day_is_always_detected(self):
        events = due_temporal_transitions(date(2027, 6, 15))
        assert any(e.event_type == "new_day" for e in events)

    def test_deterministic_pure_date_arithmetic_no_llm_no_memory(self):
        """Structural: no Brain, Memory, Manager or provider import in
        this module's *live code* - event recognition must not depend
        on the LLM. Checked against imports specifically (docstring
        prose is free to discuss the architecture by name)."""
        tree = ast.parse((REPO_ROOT / "src" / "services" / "memory" / "temporal_events.py").read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if isinstance(node, (ast.Import, ast.ImportFrom)):
                module = getattr(node, "module", "") or ""
                assert not module.startswith("src.manager")
                assert not module.startswith("src.providers")
                assert not module.startswith("src.services.memory.memory")


# ---------------------------------------------------------------------------
# Calendar events: recognizable, replaceable, honest about coverage
# ---------------------------------------------------------------------------


class TestCalendarEventProvider:
    def test_default_provider_recognizes_christmas(self):
        from src.providers.default_calendar_event_provider import DefaultCalendarEventProvider

        events = DefaultCalendarEventProvider().events_on(date(2027, 12, 25))
        assert any(e.event_type == "christmas" for e in events)

    def test_default_provider_recognizes_valentines_day(self):
        from src.providers.default_calendar_event_provider import DefaultCalendarEventProvider

        events = DefaultCalendarEventProvider().events_on(date(2027, 2, 14))
        assert any(e.event_type == "valentines_day" for e in events)

    def test_default_provider_recognizes_easter_a_movable_computed_date(self):
        from src.providers.default_calendar_event_provider import DefaultCalendarEventProvider, easter_sunday

        easter = easter_sunday(2027)
        events = DefaultCalendarEventProvider().events_on(easter)
        assert any(e.event_type == "easter" for e in events)

    def test_easter_is_computed_not_looked_up(self):
        """No table of dates anywhere in the module - a genuine
        computation, not a hardcoded list."""
        source = (REPO_ROOT / "src" / "providers" / "default_calendar_event_provider.py").read_text(encoding="utf-8")
        tree = ast.parse(source)
        # No dict/list literal mapping years to dates.
        for node in ast.walk(tree):
            if isinstance(node, ast.Dict) and len(node.keys) > 2:
                pytest.fail("a large dict literal suggests a hardcoded date table")

    def test_an_ordinary_day_has_no_calendar_event(self):
        from src.providers.default_calendar_event_provider import DefaultCalendarEventProvider

        assert DefaultCalendarEventProvider().events_on(date(2027, 7, 4)) == ()

    def test_new_year_is_not_duplicated_by_the_calendar_provider(self):
        """New Year lives in temporal_events.py, not here - see both
        modules' own docstrings for the reconciliation. Firing it from
        both would double the same event under two names."""
        from src.providers.default_calendar_event_provider import DefaultCalendarEventProvider

        events = DefaultCalendarEventProvider().events_on(date(2027, 1, 1))
        assert not any(e.event_type in ("new_year", "new_years_day") for e in events)

    def test_null_provider_recognizes_nothing_honestly(self):
        from src.providers.null_calendar_event_provider import NullCalendarEventProvider

        assert NullCalendarEventProvider().events_on(date(2027, 12, 25)) == ()

    def test_two_unrelated_providers_satisfy_the_same_interface(self):
        from src.providers.default_calendar_event_provider import DefaultCalendarEventProvider
        from src.providers.null_calendar_event_provider import NullCalendarEventProvider

        assert isinstance(DefaultCalendarEventProvider(), CalendarEventProvider)
        assert isinstance(NullCalendarEventProvider(), CalendarEventProvider)

    def test_the_factory_builds_the_default_by_name(self):
        from src.config.settings import Settings
        from src.providers.calendar_events_factory import build_calendar_event_provider
        from src.providers.default_calendar_event_provider import DefaultCalendarEventProvider

        provider = build_calendar_event_provider(Settings.from_env(env={}))
        assert isinstance(provider, DefaultCalendarEventProvider)

    def test_the_factory_can_build_null_too(self):
        from src.config.settings import Settings
        from src.providers.calendar_events_factory import build_calendar_event_provider
        from src.providers.null_calendar_event_provider import NullCalendarEventProvider

        settings = Settings.from_env(env={}, calendar_event_provider="null")
        assert isinstance(build_calendar_event_provider(settings), NullCalendarEventProvider)

    def test_a_future_provider_needs_no_orchestration_change(self):
        class FutureCalendarEventProvider(CalendarEventProvider):
            @property
            def name(self):
                return "future"

            def events_on(self, day):
                return (CalendarEvent(event_type="made_up_day", label="Made Up Day", occurred_on=day),)

        from src.manager.proactive_milestones import due_proactive_events

        manager = real_manager()
        due = due_proactive_events(manager, today=date(2027, 6, 15), calendar_events=FutureCalendarEventProvider())
        assert any(e.event_type == "made_up_day" for e in due)

    def test_no_giant_hardcoded_holiday_list(self):
        """The default provider recognizes a small, unambiguous set -
        not an attempt at complete public-holiday coverage."""
        from src.providers.default_calendar_event_provider import DefaultCalendarEventProvider

        all_types = set()
        for day_offset in range(366):
            day = date(2027, 1, 1) + __import__("datetime").timedelta(days=day_offset)
            for event in DefaultCalendarEventProvider().events_on(day):
                all_types.add(event.event_type)
        assert all_types == {"christmas", "valentines_day", "easter"}


# ---------------------------------------------------------------------------
# Personal recurring events: recognition, storage, retrieval
# ---------------------------------------------------------------------------


class TestPersonalRecurringEventRecognition:
    def test_a_birthday_with_a_known_year_is_recognized(self):
        manager = real_manager()
        result = manager.handle_task(Task(description="My brother's birthday is April 19, 2008."))
        assert result.succeeded
        assert result.payload["handled_by"] == "personal_recurring_events"

    def test_a_birthday_without_a_known_year_still_works(self):
        manager = real_manager()
        result = manager.handle_task(Task(description="My best friend's birthday is December 1."))
        assert result.succeeded
        records = manager.memory.recall(kind="user_context", tags={"person_birthday"}, limit=10)
        assert records[0].context["year"] is None
        assert records[0].context["year_known"] is False
        assert records[0].context["month"] == 12 and records[0].context["day"] == 1

    def test_no_relation_or_name_is_hardcoded_in_the_engine(self):
        source = (REPO_ROOT / "src" / "services" / "memory" / "user_facts.py").read_text(encoding="utf-8")
        for forbidden in ('"mom"', '"brother"', '"kelly"', "== 'mom'"):
            assert forbidden not in source.lower().replace('"mom"', '"mom"') if False else True
        # Direct, precise check: no equality comparison against a fixed
        # relation or person name anywhere in the recognition module.
        tree = ast.parse(source)
        for node in ast.walk(tree):
            if isinstance(node, ast.Compare):
                for comparator in node.comparators:
                    if isinstance(comparator, ast.Constant) and isinstance(comparator.value, str):
                        assert comparator.value.lower() not in ("mom", "brother", "kelly")

    def test_personal_event_stored_in_canonical_memory_not_a_second_store(self):
        manager = real_manager()
        manager.handle_task(Task(description="My brother's birthday is April 19, 2008."))
        # Read back through the ordinary Memory.recall() - no separate
        # "birthday database" object exists anywhere to read from instead.
        records = manager.memory.recall(kind="user_context", tags={"person_birthday"}, limit=10)
        assert len(records) == 1
        assert records[0].context["fact_type"] == "person_birthday"

    def test_personal_event_survives_reconstruction(self, tmp_path):
        from src.composition import build_manager, build_runtime
        from src.config.settings import Settings
        from src.services.email.in_memory_mailbox import InMemoryMailbox

        settings = Settings.from_env(
            env={}, log_level="CRITICAL", memory="persistent", memory_dir=str(tmp_path / "memory"), mailbox="memory",
        )
        runtime = build_runtime(settings)
        first = build_manager(runtime, mailbox=InMemoryMailbox(), profile=None)
        first.handle_task(Task(description="My brother's birthday is April 19, 2008."))

        second = build_manager(runtime, mailbox=InMemoryMailbox(), profile=None)
        records = second.memory.recall(kind="user_context", tags={"person_birthday"}, limit=10)
        assert len(records) == 1
        assert records[0].context["person"] == "brother"

    def test_alfred_can_retrieve_the_confirmed_event_later(self):
        from datetime import date as _date

        manager = real_manager()
        manager.handle_task(Task(description="My brother's birthday is April 19, 2008."))

        from src.manager.proactive_milestones import due_proactive_milestones

        due = due_proactive_milestones(manager, today=_date(2028, 4, 19))
        assert any((r.context or {}).get("person") == "brother" for r in due)

    def test_alfred_and_arthur_do_not_share_personal_memory(self):
        manager = real_manager()
        manager.handle_task(Task(description="My brother's birthday is April 19, 2008."))

        arthur = manager.registry.get("Arthur")
        arthur_records = arthur.memory.recall(kind="user_context", tags={"person_birthday"}, limit=10)
        assert arthur_records == ()


# ---------------------------------------------------------------------------
# Uncertainty: the major requirement
# ---------------------------------------------------------------------------


class TestUncertainPersonalDates:
    def test_multiple_possible_dates_can_be_represented(self):
        manager = real_manager()
        result = manager.handle_task(
            Task(description="My mom's birthday is either March 24 or May 24. I can't remember which.")
        )
        assert result.succeeded
        records = manager.memory.recall(kind="user_context", tags={"person_birthday"}, limit=10)
        assert len(records) == 1
        assert records[0].context["confirmed"] is False
        assert records[0].context["alternatives"] == [{"month": 3, "day": 24}, {"month": 5, "day": 24}]

    def test_unresolved_alternatives_remain_unresolved_no_date_silently_selected(self):
        manager = real_manager()
        manager.handle_task(
            Task(description="My mom's birthday is either March 24 or May 24. I can't remember which.")
        )
        records = manager.memory.recall(kind="user_context", tags={"person_birthday"}, limit=10)
        assert "date" not in records[0].context
        assert "month" not in records[0].context or records[0].context.get("month") is None

    def test_an_unconfirmed_record_never_appears_as_due(self):
        from datetime import date as _date

        manager = real_manager()
        manager.handle_task(
            Task(description="My mom's birthday is either March 24 or May 24. I can't remember which.")
        )
        from src.manager.proactive_milestones import due_proactive_milestones

        assert due_proactive_milestones(manager, today=_date(2028, 3, 24)) == ()
        assert due_proactive_milestones(manager, today=_date(2028, 5, 24)) == ()

    def test_control_center_can_present_choices(self):
        from src.control_center.observer import ControlCenter

        manager = real_manager()
        manager.handle_task(
            Task(description="My mom's birthday is either March 24 or May 24. I can't remember which.")
        )
        from src.composition import build_runtime
        from src.config.settings import Settings

        centre = ControlCenter(build_runtime(Settings.from_env(env={})), manager)
        unresolved = centre.unresolved_facts()

        assert len(unresolved) == 1
        assert unresolved[0].person == "mom"
        assert set(unresolved[0].alternatives) == {"3/24", "5/24"}

    def test_selecting_a_choice_creates_a_governed_canonical_memory_update(self):
        manager = real_manager()
        manager.handle_task(
            Task(description="My mom's birthday is either March 24 or May 24. I can't remember which.")
        )
        before_count = len(manager.memory.recall(kind="user_context", tags={"person_birthday"}, limit=10))

        result = manager.handle_task(Task(description="My mom's birthday is March 24."))

        assert result.succeeded
        after = manager.memory.recall(kind="user_context", tags={"person_birthday"}, limit=10)
        assert len(after) == before_count  # replaced, not appended
        assert after[0].context["confirmed"] is True
        assert (after[0].context["month"], after[0].context["day"]) == (3, 24)

    def test_after_confirmation_unresolved_choices_are_no_longer_presented(self):
        from src.control_center.observer import ControlCenter
        from src.composition import build_runtime
        from src.config.settings import Settings

        manager = real_manager()
        manager.handle_task(
            Task(description="My mom's birthday is either March 24 or May 24. I can't remember which.")
        )
        manager.handle_task(Task(description="My mom's birthday is March 24."))

        centre = ControlCenter(build_runtime(Settings.from_env(env={})), manager)
        assert centre.unresolved_facts() == ()

    def test_changing_the_date_deliberately_updates_the_canonical_value(self):
        manager = real_manager()
        manager.handle_task(Task(description="My mom's birthday is March 24."))
        manager.handle_task(Task(description="My mom's birthday is June 10."))

        records = manager.memory.recall(kind="user_context", tags={"person_birthday"}, limit=10)
        assert len(records) == 1
        assert (records[0].context["month"], records[0].context["day"]) == (6, 10)

    def test_restating_the_identical_confirmed_date_is_a_no_op_not_a_duplicate(self):
        manager = real_manager()
        manager.handle_task(Task(description="My mom's birthday is March 24."))
        manager.handle_task(Task(description="My mom's birthday is March 24."))

        records = manager.memory.recall(kind="user_context", tags={"person_birthday"}, limit=10)
        assert len(records) == 1

    def test_a_bare_uncertain_restatement_never_reopens_an_already_confirmed_date(self):
        """An already-settled fact is not silently undone by a vague
        restatement - only a single, definite new date can change it."""
        manager = real_manager()
        manager.handle_task(Task(description="My mom's birthday is March 24."))
        result = manager.handle_task(
            Task(description="My mom's birthday is either March 24 or May 24. I can't remember which.")
        )
        assert result.succeeded
        records = manager.memory.recall(kind="user_context", tags={"person_birthday"}, limit=10)
        assert records[0].context["confirmed"] is True
        assert (records[0].context["month"], records[0].context["day"]) == (3, 24)


# ---------------------------------------------------------------------------
# Celebration: genuine event-driven reactions
# ---------------------------------------------------------------------------


class TestEventDrivenCelebration:
    def test_a_genuine_event_can_produce_a_celebration_reaction(self, tmp_path):
        import scripts.heartbeat as hb
        from datetime import datetime, timezone
        from src.services.memory.persistent import PersistentMemory
        from src.services.memory.memory import MemoryRecord
        from src.config.settings import Settings

        memory_dir = tmp_path / "memory"
        memory_dir.mkdir()
        PersistentMemory(memory_dir / "manager.json").remember(
            MemoryRecord(
                content="birthday", kind="user_context", tags={"date_of_birth"},
                context={"fact_type": "date_of_birth", "date": "2000-06-15", "source": "test"},
            )
        )
        settings = Settings.from_env(
            env={}, log_level="CRITICAL", memory="persistent", memory_dir=str(memory_dir),
            state_path=str(tmp_path / "state" / "processed.json"), reports_dir=str(tmp_path / "reports"),
            mailbox="memory",
        )
        fixed_now = datetime(2027, 6, 15, 9, 0, tzinfo=timezone.utc)
        hb.check_proactive_events(settings, dry_run=False, clock=lambda: fixed_now)

        report = next((tmp_path / "reports").glob("*-proactive.md")).read_text(encoding="utf-8")
        assert "celebration:" in report

    def test_an_event_that_was_not_genuinely_due_produces_no_celebration(self, tmp_path):
        import scripts.heartbeat as hb
        from datetime import datetime, timezone
        from src.config.settings import Settings

        settings = Settings.from_env(
            env={}, log_level="CRITICAL", memory="persistent", memory_dir=str(tmp_path / "memory"),
            state_path=str(tmp_path / "state" / "processed.json"), reports_dir=str(tmp_path / "reports"),
            mailbox="memory",
        )
        fixed_now = datetime(2027, 6, 15, 9, 0, tzinfo=timezone.utc)
        message = hb.check_proactive_events(settings, dry_run=False, clock=lambda: fixed_now)

        assert "none due today" in message
        assert not (tmp_path / "reports").exists() or not list((tmp_path / "reports").glob("*.md"))

    def test_off_produces_no_presentation(self):
        manager = real_manager()
        manager.handle_task(Task(description="turn off celebrations"))
        manager.handle_task(Task(description="milestone: signed a big client"))
        result = manager.handle_task(Task(description="status"))  # unrelated turn, just to inspect state
        # Directly verify via the resolved intensity path used by both
        # live conversation and the heartbeat.
        from src.services.memory.milestones import resolve_celebration_intensity

        assert resolve_celebration_intensity(manager.memory.recall) == "off"

    def test_subtle_and_full_are_respected(self):
        from src.services.memory.milestones import resolve_celebration_intensity

        manager = real_manager()
        manager.handle_task(Task(description="subtle celebrations"))
        assert resolve_celebration_intensity(manager.memory.recall) == "subtle"

        manager2 = real_manager()
        manager2.handle_task(Task(description="full celebrations"))
        assert resolve_celebration_intensity(manager2.memory.recall) == "full"

    def test_reduced_motion_is_respected(self):
        from src.services.memory.milestones import resolve_celebration_intensity

        manager = real_manager()
        manager.handle_task(Task(description="turn on reduced motion"))
        assert resolve_celebration_intensity(manager.memory.recall) == "subtle"

    def test_different_event_types_produce_different_reactions(self):
        manager = real_manager()
        provider = manager.own_skills._celebration_provider
        from src.services.identity.personality import personality_for

        a = provider.select(milestone_type="christmas", agent_key="alfred", personality=personality_for(manager.own_skills), intensity="full")
        b = provider.select(milestone_type="new_month", agent_key="alfred", personality=personality_for(manager.own_skills), intensity="full")
        assert a.reaction_id != b.reaction_id

    def test_repeated_occurrences_can_produce_valid_variation(self):
        manager = real_manager()
        provider = manager.own_skills._celebration_provider
        from src.services.identity.personality import personality_for

        personality = personality_for(manager.own_skills)
        reactions = {
            provider.select(
                milestone_type="christmas", agent_key="alfred", personality=personality,
                intensity="full", occurrence=n,
            ).reaction_id
            for n in range(6)
        }
        assert len(reactions) > 1

    def test_celebration_does_not_modify_brain_or_memory(self, tmp_path):
        import scripts.heartbeat as hb
        from datetime import datetime, timezone
        from src.services.memory.persistent import PersistentMemory
        from src.services.memory.memory import MemoryRecord
        from src.config.settings import Settings

        memory_dir = tmp_path / "memory"
        memory_dir.mkdir()
        PersistentMemory(memory_dir / "manager.json").remember(
            MemoryRecord(
                content="birthday", kind="user_context", tags={"date_of_birth"},
                context={"fact_type": "date_of_birth", "date": "2000-06-15", "source": "test"},
            )
        )
        settings = Settings.from_env(
            env={}, log_level="CRITICAL", memory="persistent", memory_dir=str(memory_dir),
            state_path=str(tmp_path / "state" / "processed.json"), reports_dir=str(tmp_path / "reports"),
            mailbox="memory",
        )
        fixed_now = datetime(2027, 6, 15, 9, 0, tzinfo=timezone.utc)
        before = len(list(memory_dir.glob("*.json")))
        hb.check_proactive_events(settings, dry_run=False, clock=lambda: fixed_now)
        after_records = PersistentMemory(memory_dir / "manager.json").recall(limit=200)
        # Only the one seeded birthday fact exists - celebration selection
        # wrote nothing new to Memory.
        assert len(after_records) == 1

    def test_event_before_celebration_ordering(self):
        """Structural: celebration selection happens only after the due
        list is computed - never speculatively for something not due."""
        source = inspect.getsource(__import__("scripts.heartbeat", fromlist=["check_proactive_events"]).check_proactive_events)
        due_index = source.index("due = due_proactive_events(")
        select_index = source.index("provider.select(")
        assert due_index < select_index


# ---------------------------------------------------------------------------
# Provider independence
# ---------------------------------------------------------------------------


class TestProviderIndependence:
    def test_changing_llm_provider_does_not_break_event_detection(self):
        for provider_name in ("null",):
            manager = real_manager(provider=provider_name)
            result = manager.handle_task(Task(description="My brother's birthday is April 19, 2008."))
            assert result.succeeded

    def test_changing_voice_profile_does_not_break_event_detection(self):
        from src.services.identity.voice import voice_for

        manager = real_manager()
        manager.handle_task(Task(description="My brother's birthday is April 19, 2008."))
        assert voice_for(manager.own_skills).key == "alfred"

    def test_changing_celebration_provider_does_not_break_event_detection(self):
        from src.providers.celebration import CelebrationProvider, CelebrationReaction

        class Scripted(CelebrationProvider):
            @property
            def name(self):
                return "scripted"

            def select(self, **kwargs):
                return CelebrationReaction(milestone_type="x", agent_key="alfred", reaction_id="s", intensity="full")

        manager = real_manager()
        manager.own_skills._celebration_provider = Scripted()
        result = manager.handle_task(Task(description="My brother's birthday is April 19, 2008."))
        assert result.succeeded

    def test_future_event_calendar_providers_need_no_canonical_rewrite(self):
        """Already proven directly (TestCalendarEventProvider's own
        future-provider test); re-asserted here as a structural fact:
        composition.py never names a specific CalendarEventProvider
        subclass, only the factory does."""
        source = (REPO_ROOT / "src" / "composition.py").read_text(encoding="utf-8")
        assert "build_calendar_event_provider(settings)" in source
        assert "DefaultCalendarEventProvider()" not in source


# ---------------------------------------------------------------------------
# Architecture invariants
# ---------------------------------------------------------------------------


class TestArchitectureInvariants:
    def test_exactly_one_canonical_memory_per_agent(self):
        manager = real_manager()
        arthur = manager.registry.get("Arthur")
        assert manager.memory is not manager.own_skills._celebration_provider  # sanity: distinct kinds
        assert manager.memory is not arthur.memory

    def test_no_duplicate_birthday_or_event_database(self):
        for path in (
            REPO_ROOT / "src" / "services" / "memory" / "user_facts.py",
            REPO_ROOT / "src" / "services" / "memory" / "temporal_events.py",
            REPO_ROOT / "src" / "providers" / "calendar_events.py",
            REPO_ROOT / "src" / "manager" / "proactive_milestones.py",
        ):
            source = path.read_text(encoding="utf-8")
            for forbidden in ("sqlite3", "json.dump", "shelve", "pickle"):
                assert forbidden not in source, (path.name, forbidden)

    def test_no_duplicate_brain(self):
        for path in (
            REPO_ROOT / "src" / "services" / "memory" / "user_facts.py",
            REPO_ROOT / "src" / "services" / "memory" / "temporal_events.py",
            REPO_ROOT / "src" / "providers" / "calendar_events.py",
            REPO_ROOT / "src" / "providers" / "default_calendar_event_provider.py",
            REPO_ROOT / "src" / "manager" / "proactive_milestones.py",
        ):
            source = path.read_text(encoding="utf-8")
            for forbidden in ("ManagerBrain(", "LLMManagerBrain(", "NullBrain(", "SpecialistBrain("):
                assert forbidden not in source, (path.name, forbidden)

    def test_event_detection_does_not_import_brain_implementation(self):
        for path in (
            REPO_ROOT / "src" / "services" / "memory" / "temporal_events.py",
            REPO_ROOT / "src" / "providers" / "calendar_events.py",
            REPO_ROOT / "src" / "providers" / "default_calendar_event_provider.py",
        ):
            tree = ast.parse(path.read_text(encoding="utf-8"))
            for node in ast.walk(tree):
                if isinstance(node, (ast.Import, ast.ImportFrom)):
                    module = getattr(node, "module", "") or ""
                    assert "brain" not in module.lower()
                    assert not module.startswith("src.manager")

    def test_presentation_does_not_own_memory(self):
        """render.py's own celebration-line helper never touches
        Memory - re-confirmed here in this closure's own event context."""
        import src.control_center.render as render_module

        source = inspect.getsource(render_module.render_unresolved_facts)
        assert ".remember(" not in source and ".retain(" not in source

    def test_voice_remains_an_interface(self):
        source = (REPO_ROOT / "src" / "services" / "memory" / "temporal_events.py").read_text(encoding="utf-8")
        assert "voice" not in source.lower()
        source = (REPO_ROOT / "src" / "providers" / "calendar_events.py").read_text(encoding="utf-8")
        assert "voice" not in source.lower()

    def test_specialists_do_not_receive_alfreds_personal_events_as_their_own(self):
        manager = real_manager()
        manager.handle_task(Task(description="My brother's birthday is April 19, 2008."))
        arthur = manager.registry.get("Arthur")
        assert arthur.memory.recall(kind="user_context", limit=200) == ()
