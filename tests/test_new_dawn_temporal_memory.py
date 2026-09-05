"""Tests for New Dawn: which durable memories belong to today.

Three concepts, kept apart throughout this file because conflating them is the
mistake the design exists to avoid:

    EphemeralMemory   temporary working memory; dies with its owner
    PersistentMemory  durable saved memory; survives restart
    New Dawn          which durable memories belong to the current local day

The load-bearing claim is that **no midnight process exists**. A record becomes
historical because the clock moved, not because anything ran - so the tests for
"offline across midnight" and "silent for a week" assert that nothing needed to
happen, rather than that some catch-up job did the right thing.

The timezone tests matter more than they look. 23:30 UTC is already tomorrow in
Lagos, so a system that reckoned days in UTC would put a late-evening memory on
the wrong side of the user's midnight.

No Gmail, no model, no network. Every store is under tmp_path.
"""

import json
from datetime import date, datetime, timedelta, timezone

import pytest

from src.agents.email_agent import EmailAgent
from src.config.settings import Settings
from src.core.contracts import Task
from src.manager.manager import ManagerAgent
from src.services.email.sample_inbox import sample_inbox
from src.services.memory.day import (
    FIXED_OFFSET_ZONES,
    Calendar,
    DayView,
    TimezoneError,
    resolve_timezone,
)
from src.services.memory.ephemeral import EphemeralMemory
from src.services.memory.factory import EMAIL_AGENT, MANAGER, build_memory
from src.services.memory.memory import MemoryRecord
from src.services.memory.persistent import PersistentMemory

LAGOS = resolve_timezone("Africa/Lagos")

# 21 Aug 2026, 12:00 Lagos = 11:00 UTC. Comfortably mid-day in both.
NOON_21 = datetime(2026, 8, 21, 11, 0, tzinfo=timezone.utc)


def at(utc: datetime):
    return lambda: utc


def record(content, when, **kwargs):
    kwargs.setdefault("kind", "delegation_outcome")
    kwargs.setdefault("tags", {"note"})
    return MemoryRecord(content=content, created_at=when, **kwargs)


def lagos_calendar(now=NOON_21):
    return Calendar(zone=LAGOS, clock=at(now))


def settings(**overrides):
    return Settings.from_env(env={}, **overrides)


# ----------------------------------------------------------------------
# Resolving the timezone
# ----------------------------------------------------------------------


class TestTimezoneResolution:
    def test_africa_lagos_resolves(self):
        assert resolve_timezone("Africa/Lagos") is not None

    def test_lagos_is_one_hour_ahead_of_utc(self):
        moment = datetime(2026, 8, 21, 12, 0, tzinfo=timezone.utc)
        assert moment.astimezone(LAGOS).hour == 13

    def test_lagos_offset_is_the_same_all_year(self):
        """WAT observes no daylight saving, which is why a fixed-offset
        fallback is exact for it rather than an approximation."""
        offsets = {
            datetime(2026, month, 15, 12, 0, tzinfo=timezone.utc).astimezone(LAGOS).utcoffset()
            for month in (1, 4, 7, 10)
        }
        assert offsets == {timedelta(hours=1)}

    def test_utc_resolves(self):
        assert resolve_timezone("UTC") is not None

    def test_an_empty_timezone_is_refused(self):
        with pytest.raises(TimezoneError):
            resolve_timezone("   ")

    def test_an_unresolvable_timezone_fails_clearly(self):
        with pytest.raises(TimezoneError) as exc:
            resolve_timezone("Mars/Olympus_Mons")
        assert "Mars/Olympus_Mons" in str(exc.value)

    def test_the_error_explains_how_to_fix_it(self):
        with pytest.raises(TimezoneError) as exc:
            resolve_timezone("Mars/Olympus_Mons")
        assert "tzdata" in str(exc.value)

    def test_only_dst_free_zones_may_be_fallbacks(self):
        """A DST zone here would be silently wrong for half the year."""
        for name in FIXED_OFFSET_ZONES:
            assert name in {"utc", "africa/lagos"}

    def test_the_setting_defaults_to_lagos(self):
        assert settings().timezone == "Africa/Lagos"

    def test_the_setting_is_not_lowercased(self):
        """IANA names are case-sensitive identifiers."""
        assert Settings(timezone="Africa/Lagos").timezone == "Africa/Lagos"

    def test_an_empty_setting_is_refused(self):
        with pytest.raises(ValueError):
            Settings(timezone="   ")

    def test_describe_reports_it(self):
        assert "timezone=Africa/Lagos" in settings().describe()


# ----------------------------------------------------------------------
# Which day is a record on
# ----------------------------------------------------------------------


class TestDayIdentification:
    def test_today_is_the_local_date(self):
        assert lagos_calendar().today() == date(2026, 8, 21)

    def test_a_records_day_comes_from_created_at(self):
        calendar = lagos_calendar()
        assert calendar.day_of(record("x", NOON_21)) == date(2026, 8, 21)

    def test_utc_and_local_dates_are_kept_separate(self):
        """23:30 UTC on the 21st is 00:30 on the 22nd in Lagos. The stored
        timestamp is untouched; only the calendar interpretation differs."""
        late = datetime(2026, 8, 21, 23, 30, tzinfo=timezone.utc)

        assert late.date() == date(2026, 8, 21)
        assert Calendar(zone=LAGOS).local_date(late) == date(2026, 8, 22)
        assert Calendar(zone=timezone.utc).local_date(late) == date(2026, 8, 21)

    def test_the_stored_timestamp_is_never_reinterpreted(self):
        item = record("x", datetime(2026, 8, 21, 23, 30, tzinfo=timezone.utc))
        Calendar(zone=LAGOS).day_of(item)
        assert item.created_at == datetime(2026, 8, 21, 23, 30, tzinfo=timezone.utc)

    def test_2259_utc_is_still_the_same_lagos_day(self):
        assert Calendar(zone=LAGOS).local_date(
            datetime(2026, 8, 21, 22, 59, 59, 900000, tzinfo=timezone.utc)
        ) == date(2026, 8, 21)

    def test_2300_utc_is_the_next_lagos_day(self):
        """23:00 UTC == 00:00 Lagos, the boundary itself."""
        assert Calendar(zone=LAGOS).local_date(
            datetime(2026, 8, 21, 23, 0, tzinfo=timezone.utc)
        ) == date(2026, 8, 22)

    def test_a_naive_timestamp_is_read_as_utc(self):
        assert Calendar(zone=LAGOS).local_date(datetime(2026, 8, 21, 23, 30)) == date(2026, 8, 22)

    def test_conversion_is_zone_aware_not_naive_arithmetic(self):
        """A zone whose offset changes must be honoured at the instant of the
        record, which naive timedelta arithmetic cannot do. Africa/Lagos has no
        DST, so a synthetic zone stands in for one that does."""
        from datetime import tzinfo as TZInfo

        class SummerShifting(TZInfo):
            """+02:00 in July, +01:00 in January - a stand-in for DST."""

            def utcoffset(self, dt):
                return timedelta(hours=2 if dt.month == 7 else 1)

            def dst(self, dt):
                return timedelta(hours=1 if dt.month == 7 else 0)

            def tzname(self, dt):
                return "SYN"

        calendar = Calendar(zone=SummerShifting())
        # 22:30 UTC in July is 00:30 next day at +02:00...
        assert calendar.local_date(datetime(2026, 7, 21, 22, 30, tzinfo=timezone.utc)) == date(
            2026, 7, 22
        )
        # ...but the same clock time in January is still 23:30 at +01:00.
        assert calendar.local_date(datetime(2026, 1, 21, 22, 30, tzinfo=timezone.utc)) == date(
            2026, 1, 21
        )


# ----------------------------------------------------------------------
# Today's active context
# ----------------------------------------------------------------------


class TestTodayFiltering:
    def _view(self, tmp_path, items, now=NOON_21):
        memory = PersistentMemory(tmp_path / "m.json")
        for item in items:
            memory.remember(item)
        return DayView(memory, lagos_calendar(now))

    def test_todays_records_are_returned(self, tmp_path):
        view = self._view(tmp_path, [record("today's work", NOON_21)])
        assert len(view.today()) == 1

    def test_yesterdays_records_are_excluded(self, tmp_path):
        view = self._view(
            tmp_path,
            [record("yesterday", NOON_21 - timedelta(days=1)), record("today", NOON_21)],
        )
        assert [r.content for r in view.today()] == ["today"]

    def test_older_records_are_excluded(self, tmp_path):
        view = self._view(
            tmp_path,
            [record("last week", NOON_21 - timedelta(days=7)), record("today", NOON_21)],
        )
        assert [r.content for r in view.today()] == ["today"]

    def test_several_records_on_one_day_are_all_today(self, tmp_path):
        view = self._view(
            tmp_path,
            [record(f"note {i}", NOON_21 + timedelta(minutes=i)) for i in range(4)],
        )
        assert len(view.today()) == 4

    def test_a_record_just_before_local_midnight_is_still_today(self, tmp_path):
        just_before = datetime(2026, 8, 21, 22, 59, tzinfo=timezone.utc)  # 23:59 Lagos
        view = self._view(tmp_path, [record("late", just_before)])
        assert len(view.today()) == 1

    def test_a_record_just_after_local_midnight_is_not_today(self, tmp_path):
        just_after = datetime(2026, 8, 21, 23, 1, tzinfo=timezone.utc)  # 00:01 on the 22nd
        view = self._view(tmp_path, [record("early tomorrow", just_after)])
        assert view.today() == ()

    def test_the_query_still_works_within_today(self, tmp_path):
        view = self._view(
            tmp_path,
            [record("python backend role", NOON_21, tags={"a"}),
             record("calendar sync failed", NOON_21, tags={"b"})],
        )
        assert [r.content for r in view.today("python")] == ["python backend role"]

    def test_kind_and_tag_filters_still_apply(self, tmp_path):
        view = self._view(
            tmp_path,
            [record("one", NOON_21, kind="lesson", tags={"x"}),
             record("two", NOON_21, kind="decision", tags={"y"})],
        )
        assert [r.content for r in view.today(kind="lesson")] == ["one"]

    def test_overfetching_still_finds_today_among_many_old_records(self, tmp_path):
        """recall() ranks across all time, so the best matches can all be old.
        The lens must look deeper rather than returning nothing.

        Deliberately adversarial: today's record is stored *first*, so recall's
        recency tie-break puts the twenty older ones ahead of it. Ask for ten
        without over-fetching and today's record never appears - which is
        exactly the failure this guards.
        """
        items = [record("shared token today", NOON_21, tags={"now"})]
        items += [record(f"shared token {i}", NOON_21 - timedelta(days=i + 1), tags={f"t{i}"})
                  for i in range(20)]
        view = self._view(tmp_path, items)

        assert [r.content for r in view.today("shared")] == ["shared token today"]

    def test_the_naive_depth_would_miss_it(self, tmp_path):
        """Proves the previous test is adversarial rather than incidental: at
        the un-overfetched depth, today's record is genuinely not there."""
        items = [record("shared token today", NOON_21, tags={"now"})]
        items += [record(f"shared token {i}", NOON_21 - timedelta(days=i + 1), tags={f"t{i}"})
                  for i in range(20)]
        view = self._view(tmp_path, items)

        shallow = view.memory.recall("shared", limit=10)
        assert "shared token today" not in [r.content for r in shallow]


# ----------------------------------------------------------------------
# History
# ----------------------------------------------------------------------


class TestHistory:
    def _view(self, tmp_path, items, now=NOON_21):
        memory = PersistentMemory(tmp_path / "m.json")
        for item in items:
            memory.remember(item)
        return DayView(memory, lagos_calendar(now))

    def test_yesterday_is_still_stored(self, tmp_path):
        view = self._view(tmp_path, [record("yesterday", NOON_21 - timedelta(days=1))])
        assert len(view.memory.recall(limit=50)) == 1

    def test_yesterday_can_be_deliberately_retrieved(self, tmp_path):
        view = self._view(
            tmp_path,
            [record("yesterday", NOON_21 - timedelta(days=1)), record("today", NOON_21)],
        )
        assert [r.content for r in view.history()] == ["yesterday"]

    def test_history_excludes_today(self, tmp_path):
        view = self._view(tmp_path, [record("today", NOON_21)])
        assert view.history() == ()

    def test_history_does_not_mutate_the_record(self, tmp_path):
        original = record("yesterday", NOON_21 - timedelta(days=1))
        view = self._view(tmp_path, [original])
        recalled = view.history()[0]
        assert recalled.created_at == original.created_at
        assert recalled.id == original.id

    def test_asking_for_history_does_not_change_today(self, tmp_path):
        view = self._view(
            tmp_path,
            [record("yesterday", NOON_21 - timedelta(days=1)), record("today", NOON_21)],
        )
        view.history()
        assert [r.content for r in view.today()] == ["today"]

    def test_a_specific_day_can_be_asked_for(self, tmp_path):
        view = self._view(
            tmp_path,
            [record("the 19th", NOON_21 - timedelta(days=2)),
             record("the 20th", NOON_21 - timedelta(days=1))],
        )
        assert [r.content for r in view.on(date(2026, 8, 19))] == ["the 19th"]

    def test_history_before_a_chosen_day(self, tmp_path):
        view = self._view(
            tmp_path,
            [record("the 19th", NOON_21 - timedelta(days=2)),
             record("the 20th", NOON_21 - timedelta(days=1))],
        )
        assert [r.content for r in view.history(before=date(2026, 8, 20))] == ["the 19th"]

    def test_the_days_present_can_be_listed(self, tmp_path):
        view = self._view(
            tmp_path,
            [record("a", NOON_21 - timedelta(days=1)), record("b", NOON_21)],
        )
        assert view.days() == (date(2026, 8, 21), date(2026, 8, 20))


# ----------------------------------------------------------------------
# New Dawn, end to end
# ----------------------------------------------------------------------


class TestNewDawn:
    def test_the_worked_example(self, tmp_path):
        """August 21: remember A and B. August 22: remember C.
        today -> C.  history -> A, B.  A and B remain persisted."""
        path = tmp_path / "m.json"

        day_one = PersistentMemory(path)
        day_one.remember(record("A", NOON_21, tags={"a"}))
        day_one.remember(record("B", NOON_21 + timedelta(hours=1), tags={"b"}))

        tomorrow = NOON_21 + timedelta(days=1)
        day_two = PersistentMemory(path)
        day_two.remember(record("C", tomorrow, tags={"c"}))

        view = DayView(day_two, lagos_calendar(tomorrow))
        assert [r.content for r in view.today()] == ["C"]
        assert sorted(r.content for r in view.history()) == ["A", "B"]
        assert len(day_two.recall(limit=50)) == 3

    def test_nothing_is_rewritten_when_the_day_turns(self, tmp_path):
        """No midnight job, no rollover, no archive - so the file on disk is
        byte-identical before and after the day changes."""
        path = tmp_path / "m.json"
        memory = PersistentMemory(path)
        memory.remember(record("A", NOON_21, tags={"a"}))
        before = path.read_bytes()

        tomorrow = NOON_21 + timedelta(days=1)
        view = DayView(PersistentMemory(path), lagos_calendar(tomorrow))
        assert view.today() == ()
        assert [r.content for r in view.history()] == ["A"]
        assert path.read_bytes() == before, "something wrote to the store at the day boundary"

    def test_the_same_record_changes_side_as_the_clock_moves(self, tmp_path):
        """One record, one file, two answers - purely because time passed."""
        memory = PersistentMemory(tmp_path / "m.json")
        memory.remember(record("A", NOON_21, tags={"a"}))

        assert len(DayView(memory, lagos_calendar(NOON_21)).today()) == 1
        assert DayView(memory, lagos_calendar(NOON_21 + timedelta(days=1))).today() == ()

    def test_offline_across_midnight_needs_no_catch_up(self, tmp_path):
        """Nothing ran overnight; the next morning is simply a new day."""
        path = tmp_path / "m.json"
        evening = datetime(2026, 8, 21, 21, 0, tzinfo=timezone.utc)
        PersistentMemory(path).remember(record("last night", evening, tags={"n"}))

        morning = datetime(2026, 8, 22, 8, 0, tzinfo=timezone.utc)
        view = DayView(PersistentMemory(path), lagos_calendar(morning))
        assert view.today() == ()
        assert [r.content for r in view.history()] == ["last night"]

    def test_a_week_of_silence_needs_no_backlog(self, tmp_path):
        path = tmp_path / "m.json"
        PersistentMemory(path).remember(record("old", NOON_21, tags={"o"}))

        much_later = NOON_21 + timedelta(days=7)
        view = DayView(PersistentMemory(path), lagos_calendar(much_later))
        assert view.today() == ()
        assert len(view.history()) == 1

    def test_a_tick_crossing_midnight_splits_by_record(self, tmp_path):
        """Records keep their own timestamps; one cycle can legitimately
        contribute to two days."""
        memory = PersistentMemory(tmp_path / "m.json")
        memory.remember(record("before", datetime(2026, 8, 21, 22, 58, tzinfo=timezone.utc)))
        memory.remember(record("after", datetime(2026, 8, 21, 23, 2, tzinfo=timezone.utc)))

        on_21 = DayView(memory, lagos_calendar(NOON_21))
        assert [r.content for r in on_21.today()] == ["before"]


# ----------------------------------------------------------------------
# Manager and Specialist
# ----------------------------------------------------------------------


class TestManagerAndSpecialist:
    def test_recall_context_returns_only_today(self, tmp_path):
        config = settings(memory="persistent", memory_dir=str(tmp_path))
        memory = build_memory(config, MANAGER)
        memory.remember(record("yesterday inbox work", NOON_21 - timedelta(days=1)))
        memory.remember(record("today inbox work", NOON_21))

        manager = ManagerAgent(memory=memory, calendar=lagos_calendar())
        notes = manager.recall_context(Task(description="inbox work"))
        assert any("today" in note for note in notes)
        assert not any("yesterday" in note for note in notes)

    def test_a_manager_with_only_old_memory_gets_no_context(self, tmp_path):
        config = settings(memory="persistent", memory_dir=str(tmp_path))
        memory = build_memory(config, MANAGER)
        memory.remember(record("ancient inbox work", NOON_21 - timedelta(days=30)))

        manager = ManagerAgent(memory=memory, calendar=lagos_calendar())
        assert manager.recall_context(Task(description="inbox work")) == ()

    def test_the_specialist_uses_the_same_rules(self, tmp_path):
        config = settings(memory="persistent", memory_dir=str(tmp_path))
        memory = build_memory(config, EMAIL_AGENT)
        memory.remember(record("yesterday opportunity", NOON_21 - timedelta(days=1)))
        memory.remember(record("today opportunity", NOON_21))

        agent = EmailAgent(sample_inbox(), memory=memory, calendar=lagos_calendar())
        notes = agent.recall_context(Task(description="opportunity"))
        assert any("today" in note for note in notes)
        assert not any("yesterday" in note for note in notes)

    def test_the_default_calendar_is_utc(self):
        """An agent given no configuration reckons in UTC, so existing callers
        keep the semantics they had."""
        assert ManagerAgent().calendar.zone is timezone.utc
        # CognitiveAgent is abstract; EmailAgent is the concrete specialist.
        assert EmailAgent(sample_inbox()).calendar.zone is timezone.utc

    def test_manager_recreation_keeps_todays_memory(self, tmp_path):
        config = settings(memory="persistent", memory_dir=str(tmp_path))

        first = ManagerAgent(memory=build_memory(config, MANAGER), calendar=lagos_calendar())
        first.memory.remember(record("todays delegation", NOON_21))
        del first

        second = ManagerAgent(memory=build_memory(config, MANAGER), calendar=lagos_calendar())
        assert second.recall_context(Task(description="delegation")) != ()

    def test_process_restart_then_correct_day_identification(self, tmp_path):
        path = tmp_path / "m.json"
        PersistentMemory(path).remember(record("survivor", NOON_21, tags={"s"}))

        restored = PersistentMemory(path)
        assert len(restored.recall(limit=10)) == 1
        assert DayView(restored, lagos_calendar()).calendar.day_of(
            restored.recall(limit=10)[0]
        ) == date(2026, 8, 21)

    def test_the_stores_stay_separate_under_the_same_rules(self, tmp_path):
        config = settings(memory="persistent", memory_dir=str(tmp_path))
        manager_memory = build_memory(config, MANAGER)
        agent_memory = build_memory(config, EMAIL_AGENT)

        manager_memory.remember(record("manager only", NOON_21, tags={"m"}))
        agent_memory.remember(record("specialist only", NOON_21, tags={"s"}))

        manager_view = DayView(manager_memory, lagos_calendar())
        agent_view = DayView(agent_memory, lagos_calendar())

        assert [r.content for r in manager_view.today()] == ["manager only"]
        assert [r.content for r in agent_view.today()] == ["specialist only"]

    def test_multiple_ticks_on_one_day_share_the_active_day(self, tmp_path):
        config = settings(memory="persistent", memory_dir=str(tmp_path))
        for minute in range(3):
            memory = build_memory(config, MANAGER)
            memory.remember(record(f"tick {minute}", NOON_21 + timedelta(minutes=minute),
                                   tags={f"t{minute}"}))

        view = DayView(build_memory(config, MANAGER), lagos_calendar())
        assert len(view.today()) == 3
        assert view.days() == (date(2026, 8, 21),)


# ----------------------------------------------------------------------
# The lens is not a store, and not a brain
# ----------------------------------------------------------------------


class TestItIsOnlyALens:
    def test_it_cannot_remember(self, tmp_path):
        view = DayView(PersistentMemory(tmp_path / "m.json"), lagos_calendar())
        for forbidden in ("remember", "forget", "clear", "save", "write"):
            assert not hasattr(view, forbidden), forbidden

    def test_it_writes_no_file_of_its_own(self, tmp_path):
        memory = PersistentMemory(tmp_path / "store" / "m.json")
        memory.remember(record("a", NOON_21, tags={"a"}))
        DayView(memory, lagos_calendar()).today()
        DayView(memory, lagos_calendar()).history()

        assert [p.name for p in (tmp_path / "store").iterdir()] == ["m.json"]

    def test_the_module_stores_nothing_and_reasons_about_nothing(self):
        import ast
        import inspect

        import src.services.memory.day as module

        imported = []
        for node in ast.walk(ast.parse(inspect.getsource(module))):
            if isinstance(node, ast.ImportFrom):
                imported.append(node.module or "")
            elif isinstance(node, ast.Import):
                imported.extend(alias.name for alias in node.names)

        for forbidden in ("provider", "brain", "anthropic", "heartbeat", "mailbox"):
            assert not any(forbidden in name.lower() for name in imported), forbidden

    def test_ephemeral_memory_is_untouched(self, tmp_path):
        """New Dawn is about durable memory; the working store is unchanged."""
        first = EphemeralMemory()
        first.remember(record("x", NOON_21, tags={"x"}))
        assert len(EphemeralMemory()) == 0

    def test_a_lens_works_over_an_ephemeral_store_too(self, tmp_path):
        """The lens depends on Memory, not on PersistentMemory."""
        memory = EphemeralMemory()
        memory.remember(record("today", NOON_21, tags={"t"}))
        memory.remember(record("old", NOON_21 - timedelta(days=3), tags={"o"}))

        assert [r.content for r in DayView(memory, lagos_calendar()).today()] == ["today"]

    def test_it_does_not_reorder_what_the_store_returned(self, tmp_path):
        """Filtering removes records; ranking is the store's job and judging
        importance is the brain's."""
        memory = PersistentMemory(tmp_path / "m.json")
        items = [record(f"shared token {i}", NOON_21 + timedelta(minutes=i), tags={f"t{i}"})
                 for i in range(4)]
        for item in items:
            memory.remember(item)

        direct = [r.id for r in memory.recall("shared", limit=50)]
        through = [r.id for r in DayView(memory, lagos_calendar()).today("shared", limit=50)]
        assert through == [i for i in direct if i in set(through)]


# ----------------------------------------------------------------------
# Production isolation
# ----------------------------------------------------------------------


class TestIsolation:
    def test_writing_to_the_real_memory_directory_is_structurally_blocked(self):
        """Stage 17.15: replaces a fragile global-state check.

        The old version asserted ``data/memory`` does not exist at all -
        which cannot tell "a test created this" apart from "this already
        exists because the real, governed live path (a genuinely served
        Avatar Studio session) was used outside this test run, exactly
        as Stage 17.13-17.14's own checkpoints did." That is real,
        intentional user data, not something a test should ever require
        the *absence* of to pass.

        The actual guarantee this suite needs - that nothing a test does
        can write into the real data/memory directory - is already
        enforced by conftest.py's own autouse ``_no_production_writes``
        fixture. This test now proves that enforcement directly, with
        isolated/ephemeral test storage of its own: constructing a real
        ``PersistentMemory`` over a path inside the real directory and
        attempting to ``remember()`` into it must raise, structurally,
        regardless of whatever the directory already legitimately
        contains.
        """
        from conftest import PRODUCTION_DATA_DIR

        canary_path = PRODUCTION_DATA_DIR / "memory" / "test-isolation-guard-canary.json"
        memory = PersistentMemory(canary_path)
        with pytest.raises(AssertionError, match="production data directory"):
            memory.remember(MemoryRecord(content="canary - must never actually be written"))
        # The guard raises before any byte reaches disk - confirmed, not
        # just assumed, so a regression in the guard's own ordering
        # (blocking mkdir but not the write, for instance) would still
        # be caught here.
        assert not canary_path.exists()

    def test_the_default_memory_dir_is_guarded(self):
        from conftest import _is_production_data

        assert _is_production_data(settings().memory_dir + "/manager.json")

    def test_heartbeat_state_is_not_involved(self):
        import ast
        import inspect

        import src.services.memory.day as module

        source = inspect.getsource(module)
        tree = ast.parse(source)
        names = [n.id for n in ast.walk(tree) if isinstance(n, ast.Name)]
        assert "StateStore" not in names
        assert "processed" not in source
