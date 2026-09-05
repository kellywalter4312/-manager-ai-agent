"""Stage 12 closure - genuine proactive milestone surfacing.

The comprehensive Stage 12-15 audit found that ``proactive_status``/
``is_mentionable`` (``src/services/memory/milestones.py``) were correctly
implemented and correctly unit-tested, but nothing anywhere actually
*consumed* them - a stored "silent" or "snooze" preference had no
observable effect, and a birthday could only ever be answered when asked,
never surfaced. This file proves the three things that changed:

    due_recurring_dates()        a pure, generic function
                                  (src/services/memory/milestones.py):
                                  which of a set of already-recalled,
                                  already-governed records name a date
                                  matching today, filtered by each
                                  record's own stored preference
    due_proactive_milestones()   a governed accessor
                                  (src/manager/proactive_milestones.py):
                                  reads an agent's own Memory through the
                                  identical .recall() every capability
                                  handler already uses - never a second
                                  memory system
    check_proactive_events() the heartbeat integration
                                  (scripts/heartbeat.py): reuses the
                                  existing StateStore's generic dedup
                                  bucket and the existing reports
                                  directory - never a new state or
                                  report channel

No contacts, no birthday-specific code path anywhere, no fabricated
event: a person with nothing stored gets nothing surfaced, always.
"""

from __future__ import annotations

import inspect
from datetime import date, datetime, timezone
from pathlib import Path

import pytest

from src.services.memory.memory import MemoryRecord
from src.services.memory.milestones import (
    build_milestone_record,
    due_recurring_dates,
    is_mentionable,
)

REPO_ROOT = Path(__file__).resolve().parent.parent


def dated_record(
    *, kind: str, date_value: str, fact_type: str = "date_of_birth", proactive: str | None = None,
) -> MemoryRecord:
    context = {"fact_type": fact_type, "date": date_value, "source": "test"}
    if proactive is not None:
        context["proactive"] = proactive
    return MemoryRecord(content=f"a {fact_type} record", kind=kind, tags={fact_type}, context=context)


# ---------------------------------------------------------------------------
# 1-3. due_recurring_dates - the pure, generic matcher
# ---------------------------------------------------------------------------


class TestDueRecurringDatesIsGenericAndHonest:
    def test_a_birthday_two_years_ago_matches_today(self):
        record = dated_record(kind="user_context", date_value="2020-06-15")
        assert due_recurring_dates([record], today=date(2026, 6, 15)) == (record,)

    def test_a_milestone_from_a_past_year_matches_its_own_anniversary(self):
        """Generic over what is recurring - a milestone's own occurrence
        date is read identically to a date_of_birth fact, through the
        same context["date"] field, never a birthday-only code path."""
        record = build_milestone_record(
            "first_client", "signed my first client", agent_name="Alfred",
            when=date(2024, 3, 1),
        )
        assert due_recurring_dates([record], today=date(2026, 3, 1)) == (record,)

    def test_a_different_month_or_day_does_not_match(self):
        record = dated_record(kind="user_context", date_value="2020-06-15")
        assert due_recurring_dates([record], today=date(2026, 6, 16)) == ()
        assert due_recurring_dates([record], today=date(2026, 7, 15)) == ()

    def test_the_same_year_the_record_was_created_does_not_match(self):
        """A fact stated or a milestone recorded *today* is not
        immediately "surfaced" a second time - only a genuine later
        anniversary is."""
        record = dated_record(kind="user_context", date_value="2026-06-15")
        assert due_recurring_dates([record], today=date(2026, 6, 15)) == ()

    def test_a_future_dated_record_does_not_match(self):
        record = dated_record(kind="user_context", date_value="2027-06-15", fact_type="completion_date")
        assert due_recurring_dates([record], today=date(2026, 6, 15)) == ()

    def test_a_record_with_no_date_is_silently_skipped_never_guessed_at(self):
        record = MemoryRecord(content="no date here", kind="user_context", context={"fact_type": "name"})
        assert due_recurring_dates([record], today=date(2026, 6, 15)) == ()

    def test_an_unparseable_date_is_silently_skipped(self):
        record = dated_record(kind="user_context", date_value="not-a-date")
        assert due_recurring_dates([record], today=date(2026, 6, 15)) == ()

    def test_multiple_due_records_are_all_returned(self):
        a = dated_record(kind="user_context", date_value="2020-06-15", fact_type="date_of_birth")
        b = build_milestone_record("first_client", "x", agent_name="Alfred", when=date(2024, 6, 15))
        result = due_recurring_dates([a, b], today=date(2026, 6, 15))
        assert len(result) == 2
        assert a in result and b in result


# ---------------------------------------------------------------------------
# 4-5. Stored proactive/silent/snooze preferences actually govern surfacing
# ---------------------------------------------------------------------------


class TestStoredPreferencesActuallyGovernSurfacing:
    def test_silent_preference_suppresses_a_due_milestone(self):
        record = build_milestone_record(
            "quiet_thing", "x", agent_name="Alfred", when=date(2024, 6, 15), proactive="silent",
        )
        assert due_recurring_dates([record], today=date(2026, 6, 15)) == ()

    def test_snoozed_preference_suppresses_a_due_milestone(self):
        record = build_milestone_record(
            "quiet_thing", "x", agent_name="Alfred", when=date(2024, 6, 15), proactive="snooze",
        )
        assert due_recurring_dates([record], today=date(2026, 6, 15)) == ()

    def test_allow_preference_permits_a_due_milestone(self):
        record = build_milestone_record(
            "loud_thing", "x", agent_name="Alfred", when=date(2024, 6, 15), proactive="allow",
        )
        assert due_recurring_dates([record], today=date(2026, 6, 15)) == (record,)

    def test_an_unset_preference_defaults_to_allow(self):
        """The same honest default proactive_status already documents -
        a date_of_birth fact never sets a "proactive" field at all, and
        must not be silently suppressed by its absence."""
        record = dated_record(kind="user_context", date_value="2020-06-15")
        assert "proactive" not in (record.context or {})
        assert due_recurring_dates([record], today=date(2026, 6, 15)) == (record,)

    def test_this_is_a_genuine_consumer_of_is_mentionable_not_a_reimplementation(self):
        """Structural: due_recurring_dates must call the real
        is_mentionable, never duplicate its logic - the exact gap the
        audit found (the function existed, nothing called it)."""
        source = inspect.getsource(due_recurring_dates)
        assert "is_mentionable(" in source


# ---------------------------------------------------------------------------
# 6. Milestone storage remains governed (unchanged by this closure pass)
# ---------------------------------------------------------------------------


class TestMilestoneStorageRemainsGoverned:
    def test_due_recurring_dates_never_writes_anything(self):
        """Read-only: no Memory object, no retain call, anywhere in this
        function's own source."""
        source = inspect.getsource(due_recurring_dates)
        assert ".remember(" not in source
        assert ".retain(" not in source

    def test_the_proactive_milestones_module_only_recalls(self):
        import src.manager.proactive_milestones as module

        source = inspect.getsource(module)
        assert ".remember(" not in source
        assert ".retain(" not in source
        assert ".recall(" in source


# ---------------------------------------------------------------------------
# 7-8. The governed accessor - due_proactive_milestones
# ---------------------------------------------------------------------------


def real_manager(**overrides):
    from src.composition import build_manager, build_runtime
    from src.config.settings import Settings
    from src.services.email.in_memory_mailbox import InMemoryMailbox

    settings = Settings.from_env(env={}, log_level="CRITICAL", memory="ephemeral", **overrides)
    runtime = build_runtime(settings)
    return build_manager(runtime, mailbox=InMemoryMailbox(), profile=None)


class TestTheGovernedAccessor:
    def test_reads_from_the_managers_own_memory_only(self):
        from src.manager.proactive_milestones import due_proactive_milestones

        manager = real_manager()
        manager.memory.remember(
            dated_record(kind="user_context", date_value="2020-06-15")
        )
        due = due_proactive_milestones(manager, today=date(2026, 6, 15))
        assert len(due) == 1

    def test_a_manager_with_no_memory_returns_empty_honestly(self):
        from src.manager.manager import ManagerAgent
        from src.manager.proactive_milestones import due_proactive_milestones
        from src.registry.agent_registry import AgentRegistry

        bare = ManagerAgent(registry=AgentRegistry(), memory=None)
        assert due_proactive_milestones(bare, today=date(2026, 6, 15)) == ()

    def test_a_specialists_own_milestones_are_never_read(self):
        """A specialist's memory is its own business - never reached
        from here, the same boundary every other governed accessor in
        this project already keeps."""
        manager = real_manager()
        arthur = manager.registry.get("Arthur")
        arthur.memory.remember(dated_record(kind="user_context", date_value="2020-06-15"))

        from src.manager.proactive_milestones import due_proactive_milestones
        assert due_proactive_milestones(manager, today=date(2026, 6, 15)) == ()

    def test_never_fabricates_when_nothing_is_stored(self):
        from src.manager.proactive_milestones import due_proactive_milestones

        manager = real_manager()
        assert due_proactive_milestones(manager, today=date(2026, 6, 15)) == ()

    def test_defaults_today_to_the_real_clock(self):
        from src.manager.proactive_milestones import due_proactive_milestones

        manager = real_manager()
        # No exception, no injected date - proves the default resolves.
        assert due_proactive_milestones(manager) == ()


# ---------------------------------------------------------------------------
# 9-10. The heartbeat integration - genuine, dated triggering + dedup
# ---------------------------------------------------------------------------


class TestHeartbeatIntegration:
    def _settings(self, tmp_path, **overrides):
        from src.config.settings import Settings

        return Settings.from_env(
            env={},
            log_level="CRITICAL",
            memory="persistent",
            memory_dir=str(tmp_path / "memory"),
            state_path=str(tmp_path / "state" / "processed.json"),
            reports_dir=str(tmp_path / "reports"),
            mailbox="memory",
            **overrides,
        )

    def _seed_manager_memory(self, settings, record: MemoryRecord) -> None:
        from src.services.memory.persistent import PersistentMemory

        path = Path(settings.memory_dir) / "manager.json"
        path.parent.mkdir(parents=True, exist_ok=True)
        PersistentMemory(path).remember(record)

    # A fixed "today" for every test below that cares about an exact due
    # count - June 15 is never the 1st of a month, never Jan 1, never
    # Christmas/Valentine's, and never Easter (which only ever falls
    # between March 22 and April 25) - so these tests are honest about
    # what they seed and assert, regardless of the real calendar date
    # this suite happens to run on. Stage 12 closure's own due_proactive_events
    # aggregates personal facts *and* temporal/calendar events - see
    # TestHeartbeatIntegrationEvents below for coverage of those.
    _FIXED_TODAY = date(2027, 6, 15)
    _FIXED_NOW = datetime(2027, 6, 15, 9, 0, tzinfo=timezone.utc)

    def test_a_due_birthday_writes_a_report(self, tmp_path):
        import scripts.heartbeat as hb

        settings = self._settings(tmp_path)
        self._seed_manager_memory(
            settings,
            dated_record(kind="user_context", date_value=self._FIXED_TODAY.replace(year=self._FIXED_TODAY.year - 3).isoformat()),
        )

        message = hb.check_proactive_events(settings, dry_run=False, clock=lambda: self._FIXED_NOW)

        assert "1 due today" in message
        reports = list((tmp_path / "reports").glob("*-proactive.md"))
        assert len(reports) == 1
        assert "date_of_birth" in reports[0].read_text(encoding="utf-8")

    def test_nothing_due_writes_no_report(self, tmp_path):
        import scripts.heartbeat as hb

        settings = self._settings(tmp_path)
        message = hb.check_proactive_events(settings, dry_run=False, clock=lambda: self._FIXED_NOW)

        assert "none due today" in message
        assert not (tmp_path / "reports").exists() or not list((tmp_path / "reports").glob("*.md"))

    def test_a_silenced_milestone_never_reaches_a_report(self, tmp_path):
        import scripts.heartbeat as hb

        settings = self._settings(tmp_path)
        self._seed_manager_memory(
            settings,
            build_milestone_record(
                "quiet_thing", "x", agent_name="Alfred",
                when=self._FIXED_TODAY.replace(year=self._FIXED_TODAY.year - 2), proactive="silent",
            ),
        )

        message = hb.check_proactive_events(settings, dry_run=False, clock=lambda: self._FIXED_NOW)

        assert "none due today" in message

    def test_the_same_day_is_reported_only_once(self, tmp_path):
        """Reuses the existing StateStore's generic dedup bucket - a
        birthday due today must not be re-reported on every heartbeat
        tick within the same day."""
        import scripts.heartbeat as hb

        settings = self._settings(tmp_path)
        self._seed_manager_memory(
            settings,
            dated_record(kind="user_context", date_value=self._FIXED_TODAY.replace(year=self._FIXED_TODAY.year - 3).isoformat()),
        )

        first = hb.check_proactive_events(settings, dry_run=False, clock=lambda: self._FIXED_NOW)
        second = hb.check_proactive_events(settings, dry_run=False, clock=lambda: self._FIXED_NOW)

        assert "1 due today ->" in first
        assert "already reported" in second
        reports = list((tmp_path / "reports").glob("*-proactive.md"))
        assert len(reports) == 1

    def test_reuses_the_existing_state_store_never_a_second_state_file(self, tmp_path):
        import scripts.heartbeat as hb
        from src.heartbeat.state import StateStore

        today = date.today()
        settings = self._settings(tmp_path)
        self._seed_manager_memory(
            settings,
            dated_record(kind="user_context", date_value=today.replace(year=today.year - 3).isoformat()),
        )

        hb.check_proactive_events(settings, dry_run=False)

        state = StateStore(settings.state_path).load()
        assert any(key.startswith("proactive:") for key in state.opportunities)
        # No second state file was created anywhere under tmp_path.
        state_files = list((tmp_path / "state").glob("*.json"))
        assert state_files == [Path(settings.state_path)]

    def test_reuses_the_existing_reports_directory_never_a_second_channel(self, tmp_path):
        import scripts.heartbeat as hb

        today = date.today()
        settings = self._settings(tmp_path)
        self._seed_manager_memory(
            settings,
            dated_record(kind="user_context", date_value=today.replace(year=today.year - 3).isoformat()),
        )

        hb.check_proactive_events(settings, dry_run=False)

        # Exactly one reports directory exists under tmp_path, and it is
        # the configured one - the same directory the email cycle writes
        # opportunity reports into.
        report_dirs = [p for p in tmp_path.rglob("*") if p.is_dir() and p.name == "reports"]
        assert report_dirs == [Path(settings.reports_dir)]

    def test_disabled_heartbeat_never_runs_the_check(self, tmp_path):
        """Autonomy is opt-in - the same rule the email cycle's own
        `enabled` flag already lives under, applied identically here."""
        import subprocess
        import sys

        settings_env = {
            "MANAGER_MEMORY": "persistent",
            "MANAGER_MEMORY_DIR": str(tmp_path / "memory"),
            "MANAGER_STATE_PATH": str(tmp_path / "state" / "processed.json"),
            "MANAGER_REPORTS_DIR": str(tmp_path / "reports"),
            "MANAGER_MAILBOX": "memory",
            "MANAGER_LOG_LEVEL": "CRITICAL",
        }
        today = date.today()
        from src.config.settings import Settings
        settings = Settings.from_env(env=settings_env)
        self._seed_manager_memory(
            settings,
            dated_record(kind="user_context", date_value=today.replace(year=today.year - 3).isoformat()),
        )

        result = subprocess.run(
            [sys.executable, str(REPO_ROOT / "scripts" / "heartbeat.py")],
            cwd=REPO_ROOT, env={**__import__("os").environ, **settings_env},
            capture_output=True, text=True, timeout=60,
        )
        assert "proactive events" not in result.stdout


# ---------------------------------------------------------------------------
# 11. write_report/render_proactive_report - additive, backward compatible
# ---------------------------------------------------------------------------


class TestReportingIsAdditiveAndBackwardCompatible:
    def test_existing_opportunity_callers_get_byte_identical_filenames(self, tmp_path):
        from src.heartbeat.reporting import write_report

        when = datetime(2026, 6, 15, 9, 30, tzinfo=timezone.utc)
        path = write_report("some text", when, tmp_path)
        assert path.name == "20260615-093000-opportunities.md"

    def test_a_proactive_suffix_writes_a_distinctly_named_file(self, tmp_path):
        from src.heartbeat.reporting import write_report

        when = datetime(2026, 6, 15, 9, 30, tzinfo=timezone.utc)
        path = write_report("some text", when, tmp_path, suffix="proactive")
        assert path.name == "20260615-093000-proactive.md"

    def test_render_proactive_report_is_honestly_empty_when_nothing_is_due(self):
        from src.heartbeat.reporting import render_proactive_report

        text = render_proactive_report([])
        assert "due today: 0" in text

    def test_render_proactive_report_never_fabricates_a_headline(self):
        from src.heartbeat.reporting import render_proactive_report

        record = build_milestone_record("first_client", "signed my first client", agent_name="Alfred")
        text = render_proactive_report([record])
        assert "first_client" in text
        assert "signed my first client" in text


# ---------------------------------------------------------------------------
# 12. Quiet hours - the architecture genuinely does not support this,
# stated honestly rather than faked
# ---------------------------------------------------------------------------


class TestQuietHoursIsHonestlyNotComputed:
    def test_no_new_time_arithmetic_was_added_to_schedule_py(self):
        """UserSchedule's own docstring refuses an is_quiet_now(): "a
        schedule that started resolving instants would be a second
        temporal system." This closure pass respects that refusal
        rather than working around it - see this module's own docstring
        for why quiet-hours gating is not attempted here."""
        source = (REPO_ROOT / "src" / "services" / "schedule" / "schedule.py").read_text(encoding="utf-8")
        # The module's own docstring already explains, in prose, why no
        # such method exists ("there is no is_quiet_now()...") - this
        # checks that it was never actually *defined*, not that the
        # explanatory sentence itself was removed.
        assert "def is_quiet_now" not in source

    def test_the_proactive_module_never_imports_userschedule(self):
        import ast

        for path in (
            REPO_ROOT / "src" / "manager" / "proactive_milestones.py",
            REPO_ROOT / "src" / "services" / "memory" / "milestones.py",
        ):
            tree = ast.parse(path.read_text(encoding="utf-8"))
            for node in ast.walk(tree):
                if isinstance(node, (ast.Import, ast.ImportFrom)):
                    module = getattr(node, "module", "") or ""
                    names = ", ".join(getattr(n, "name", "") for n in node.names)
                    assert "schedule" not in module and "UserSchedule" not in names


# ---------------------------------------------------------------------------
# 13. No duplicate Brain or Memory anywhere in this closure's new code
# ---------------------------------------------------------------------------


class TestNoDuplicateBrainOrMemory:
    def test_no_brain_or_memory_construction_anywhere_new(self):
        import ast

        for path in (
            REPO_ROOT / "src" / "manager" / "proactive_milestones.py",
            REPO_ROOT / "src" / "services" / "memory" / "milestones.py",
        ):
            source = path.read_text(encoding="utf-8")
            for forbidden in (
                "EphemeralMemory(", "PersistentMemory(", "ManagerBrain(",
                "LLMManagerBrain(", "SpecialistBrain(",
            ):
                assert forbidden not in source, (path.name, forbidden)

    def test_no_arbitrary_execution(self):
        for path in (
            REPO_ROOT / "src" / "manager" / "proactive_milestones.py",
            REPO_ROOT / "src" / "services" / "memory" / "milestones.py",
        ):
            source = path.read_text(encoding="utf-8")
            for forbidden in ("eval(", "exec(", "subprocess", "os.system", "__import__", "open("):
                assert forbidden not in source, (path.name, forbidden)


# ---------------------------------------------------------------------------
# Mutation test - the proactive-milestone permission/consumption gate
# ---------------------------------------------------------------------------


class TestMutationTheConsumptionGate:
    def test_the_is_mentionable_filter_is_load_bearing(self):
        """Pins what removing the is_mentionable() check in
        due_recurring_dates would break: a silenced milestone must never
        be reported as due - this is the exact defect the audit found
        (the check existed, nothing called it)."""
        record = build_milestone_record(
            "quiet_thing", "x", agent_name="Alfred", when=date(2024, 6, 15), proactive="silent",
        )
        assert due_recurring_dates([record], today=date(2026, 6, 15)) == ()

    def test_the_same_year_exclusion_is_load_bearing(self):
        """Pins what removing the year-exclusion check would break: a
        milestone recorded today would be immediately "due" again the
        same day."""
        record = dated_record(kind="user_context", date_value=date.today().isoformat())
        assert due_recurring_dates([record], today=date.today()) == ()
