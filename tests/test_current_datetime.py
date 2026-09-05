"""Foundation Repair #1 - current day/date/month/year, answered honestly
and deterministically, in both Half Brain and Full Brain.

The defect this closes, measured by the audit rather than suspected:
"what day is it", "what year is it" and their kin either scored a plain
acknowledgement word ("it") and were answered with a false-success "Got
it.", or scored "today" against LIVE_INFORMATION and were refused as
needing a live backend they never needed - the calendar date requires no
network and no model. See src/manager/capabilities.py's own "Foundation
Repair #1" section for the full account.

Every test here uses a fixed, injected clock - the same Calendar pattern
src/services/memory/day.py's own tests already use - so nothing depends
on the day this suite happens to run.
"""

from __future__ import annotations

from datetime import datetime, timezone

import pytest

from src.core.contracts import Task
from src.manager.capabilities import CURRENT_DATETIME, MANAGER_CAPABILITIES, select_capability
from src.manager.cognition import CognitiveMode, assess_manager
from src.manager.llm_brain import LLMManagerBrain
from src.manager.manager import ManagerAgent
from src.manager.manager_skills import ManagerSkills
from src.providers.base import Completion, LLMProvider
from src.services.identity.identity import load_identity
from src.services.memory.day import Calendar

IDENTITY = load_identity("IDENTITY.md")

# A fixed instant, deliberately not "today" - 2026-12-25 is a Friday.
FIXED_INSTANT = datetime(2026, 12, 25, 10, 0, 0, tzinfo=timezone.utc)


def fixed_calendar(instant: datetime = FIXED_INSTANT) -> Calendar:
    return Calendar(zone=timezone.utc, clock=lambda: instant)


def alfred(brain=None, calendar=None, **kwargs):
    """A Manager with Alfred's real identity, an explicit Calendar, and -
    when a brain is given - converse wired the way composition.py's real
    build_manager wires it (converse=brain.converse). ManagerAgent.brain
    and ManagerSkills.converse are two separately-settable collaborators;
    a bare construction that forgot this would silently test Half Brain
    behaviour while believing it had a working Full Brain."""
    manager = ManagerAgent(brain=brain, calendar=calendar)
    manager.own_skills = ManagerSkills(
        IDENTITY,
        roster=manager.registry.all_agents,
        calendar=calendar,
        converse=manager.brain.converse,
        # The same store ManagerAgent itself uses, bound the way
        # composition.py's real build_manager binds it - so a regression
        # check against a *different* capability (temporal_derivation)
        # gets that capability's real "no date on record" refusal rather
        # than a "no recall wired" artefact of this test fixture.
        recall=manager.memory.recall,
        **kwargs,
    )
    manager.registry.register(manager.own_skills)
    return manager


class WorkingProvider(LLMProvider):
    """A provider that would genuinely answer, if ever consulted.

    Records every call it receives, so a test can assert current_datetime
    never spent one - the deterministic answer must come from the
    calendar, never from this."""

    def __init__(self) -> None:
        self.calls: list[str] = []

    @property
    def name(self) -> str:
        return "anthropic"

    def complete(self, prompt: str, *, system: str | None = None) -> Completion:
        self.calls.append(prompt)
        if system and "Reply with a single JSON object" in system:
            return Completion(text='{"action":"unknown","reason":"n/a"}', provider="anthropic")
        return Completion(text="I would have to guess.", provider="anthropic")


def full_brain_with_working_provider() -> tuple[LLMManagerBrain, WorkingProvider]:
    provider = WorkingProvider()
    brain = LLMManagerBrain(provider)
    brain.deliberate(Task(description="warm up"))  # the one call that proves usability
    provider.calls.clear()  # the warm-up call is not part of what a test below counts
    return brain, provider


# ---------------------------------------------------------------------------
# A. Current date
# ---------------------------------------------------------------------------


class TestCurrentDate:
    @pytest.mark.parametrize(
        "phrasing",
        [
            "what is today's date?",
            "what's the date today?",
            "what is the date today",
            "today's date",
            "current date",
            "what date is it",
        ],
    )
    def test_answers_the_real_date_half_brain(self, phrasing):
        manager = alfred(calendar=fixed_calendar())
        result = manager.handle_task(Task(description=phrasing))
        assert result.succeeded, result.error
        assert result.payload["handled_by"] == "current_datetime"
        assert result.payload["derived"] == "date"
        assert result.payload["value"] == "2026-12-25"
        assert "December 25, 2026" in result.output

    def test_half_brain_mode_confirmed(self):
        manager = alfred(calendar=fixed_calendar())
        assert assess_manager(manager).mode is CognitiveMode.HALF_BRAIN
        result = manager.handle_task(Task(description="what is today's date?"))
        assert result.succeeded


# ---------------------------------------------------------------------------
# B. Current day
# ---------------------------------------------------------------------------


class TestCurrentDay:
    @pytest.mark.parametrize(
        "phrasing",
        [
            "what day is today?",
            "what day is it?",
            "what day is it",
            "what day of the week is it",
            "which day is it",
        ],
    )
    def test_answers_the_real_day_half_brain(self, phrasing):
        manager = alfred(calendar=fixed_calendar())
        result = manager.handle_task(Task(description=phrasing))
        assert result.succeeded, result.error
        assert result.payload["handled_by"] == "current_datetime"
        assert result.payload["derived"] == "day"
        assert result.output == "Today is Friday."

    def test_no_false_got_it_success(self):
        """The exact defect the audit measured: "Got it." standing in for
        the real answer."""
        manager = alfred(calendar=fixed_calendar())
        result = manager.handle_task(Task(description="what day of the week is it"))
        assert result.output != "Got it."
        assert result.output == "Today is Friday."


# ---------------------------------------------------------------------------
# C. Current year / D. Current month
# ---------------------------------------------------------------------------


class TestCurrentYearAndMonth:
    def test_current_year(self):
        manager = alfred(calendar=fixed_calendar())
        result = manager.handle_task(Task(description="what year is it?"))
        assert result.succeeded
        assert result.output == "It's 2026."
        assert result.payload["derived"] == "year"

    def test_current_year_previously_misrouted_to_small_talk(self):
        """Regression pin: this exact phrasing used to score only the
        acknowledgement word "it" and be answered by small_talk."""
        manager = alfred(calendar=fixed_calendar())
        result = manager.handle_task(Task(description="what year is it"))
        assert result.payload["handled_by"] == "current_datetime"

    def test_current_month(self):
        manager = alfred(calendar=fixed_calendar())
        result = manager.handle_task(Task(description="what month is it?"))
        assert result.succeeded
        assert result.output == "It's December."
        assert result.payload["derived"] == "month"


# ---------------------------------------------------------------------------
# E. Natural variants routed by select_capability directly
# ---------------------------------------------------------------------------


class TestRoutingItself:
    @pytest.mark.parametrize(
        "phrasing",
        [
            "what day is it",
            "what day of the week is it",
            "what is today's date",
            "what's the date today",
            "todays date",
            "what year is it",
            "what's the year",
            "what month is it",
            "which month is it",
        ],
    )
    def test_select_capability_reaches_current_datetime(self, phrasing):
        capability = select_capability(Task(description=phrasing), MANAGER_CAPABILITIES, participants=[])
        assert capability.name == "current_datetime"

    def test_registered_in_the_real_manager_capabilities_registry(self):
        assert MANAGER_CAPABILITIES["current_datetime"] is CURRENT_DATETIME


# ---------------------------------------------------------------------------
# Half Brain requirement: no provider configured at all
# ---------------------------------------------------------------------------


class TestHalfBrainIndependence:
    def test_works_with_no_brain_configured(self):
        manager = alfred(calendar=fixed_calendar())  # brain=None -> NullBrain
        assert assess_manager(manager).mode is CognitiveMode.HALF_BRAIN
        result = manager.handle_task(Task(description="what day is it"))
        assert result.succeeded
        assert result.output == "Today is Friday."


# ---------------------------------------------------------------------------
# Full Brain requirement: a genuinely usable model must not be consulted
# ---------------------------------------------------------------------------


class TestFullBrainDoesNotSpendACall:
    def test_full_brain_still_answers_deterministically_without_a_call(self):
        brain, provider = full_brain_with_working_provider()
        manager = alfred(brain=brain, calendar=fixed_calendar())
        assert assess_manager(manager).mode is CognitiveMode.FULL_BRAIN

        result = manager.handle_task(Task(description="what day is it"))

        assert result.succeeded
        assert result.output == "Today is Friday."
        assert result.payload["handled_by"] == "current_datetime"
        # The decisive assertion: a verified, usable model was available
        # and was never asked - the deterministic answer came from the
        # calendar, exactly as it does in Half Brain.
        assert provider.calls == []

    def test_full_brain_and_half_brain_give_the_identical_answer(self):
        half = alfred(calendar=fixed_calendar())
        brain, _ = full_brain_with_working_provider()
        full = alfred(brain=brain, calendar=fixed_calendar())

        task = Task(description="what is today's date?")
        assert half.handle_task(task).output == full.handle_task(task).output


# ---------------------------------------------------------------------------
# Regression protection: neighbouring capabilities are untouched
# ---------------------------------------------------------------------------


class TestRegressionProtection:
    def test_temporal_derivation_still_answers_age_questions(self):
        """The stored-fact-derived capability this one sits beside must
        be completely unaffected."""
        manager = alfred(calendar=fixed_calendar())
        result = manager.handle_task(Task(description="how old am i"))
        assert not result.succeeded
        assert "date of birth" in result.error

    def test_small_talk_still_handles_thanks_and_greetings(self):
        manager = alfred(calendar=fixed_calendar())
        assert manager.handle_task(Task(description="thanks")).output == "You're welcome!"
        greeting = manager.handle_task(Task(description="hello")).output
        assert greeting and "Got it." != greeting

    def test_a_stored_birthday_question_is_not_hijacked(self):
        """"When is my birthday" names a day-shaped question but is
        temporal_derivation's, not current_datetime's - the countdown is
        computed from a stored fact, not answered from the calendar
        alone."""
        manager = alfred(calendar=fixed_calendar())
        result = manager.handle_task(Task(description="when is my birthday"))
        assert result.payload is None or result.payload.get("handled_by") != "current_datetime"

    def test_an_unrelated_date_flavoured_question_is_not_a_fake_success(self):
        """"What's the date of Easter" is not a self-knowledge question -
        it names a subject. Whichever capability it falls to, it must
        never be answered with today's date, and never with "Got it.".
        """
        manager = alfred(calendar=fixed_calendar())
        result = manager.handle_task(Task(description="what's the date of easter"))
        assert result.output != "Got it."
        if result.payload is not None:
            assert result.payload.get("value") != "2026-12-25"

    def test_full_suite_of_temporal_derivation_markers_untouched(self):
        manager = alfred(calendar=fixed_calendar())
        for phrasing in ("how old am i", "when is my birthday", "when do i graduate"):
            result = manager.handle_task(Task(description=phrasing))
            # All three correctly refuse (no stored date in this fixture) -
            # the point is that they still reach temporal_derivation at
            # all, not current_datetime.
            assert result.payload is None
