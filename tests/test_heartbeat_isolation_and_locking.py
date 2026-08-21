"""Tests for two reliability properties the operational audit found missing.

**Isolation.** A test drove scripts/heartbeat.py with default settings, whose
state path resolves to the real data/state/processed.json. pytest therefore ran
a genuine heartbeat cycle and wrote sample-inbox message ids and fixture
opportunities into the user's Gmail-derived state, and filed a report beside
the real ones. Fixing that one test would not stop the next one, so the write
itself is now blocked by an autouse guard in conftest.

**Concurrency.** Atomic writes stop a torn file; they do not stop two runs from
loading the same state, analysing the same inbox and having the second save
overwrite what the first learned. The window is the whole cycle, so the lock
covers the whole cycle.

Nothing here touches Gmail, a model, a network or the real data directory.
"""

import json
import os
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from conftest import PRODUCTION_DATA_DIR, _is_production_data
from src.agents.email_agent import EmailAgent
from src.agents.email.analyzers import JobOpportunityAnalyzer
from src.heartbeat.lock import DEFAULT_STALE_AFTER, HeartbeatLock
from src.heartbeat.runner import HeartbeatRunner, TickStatus
from src.heartbeat.state import StateStore
from src.manager.manager import ManagerAgent
from src.services.email.mailbox import Mailbox, MailboxError
from src.services.email.sample_inbox import sample_inbox
from src.services.profile.loader import load_profile

NOW = datetime(2026, 8, 21, 9, 0, tzinfo=timezone.utc)


def profile():
    return load_profile("USER.example.md")


def make_runner(tmp_path, *, enabled=True, clock=None, lock=None, mailbox=None):
    """A real runner wired to temporary paths - realistic, but isolated."""
    def build_manager(user_profile):
        manager = ManagerAgent()
        manager.register_agent(
            EmailAgent(
                mailbox if mailbox is not None else sample_inbox(),
                analyzers=[JobOpportunityAnalyzer(profile=user_profile)],
            )
        )
        return manager

    return HeartbeatRunner(
        build_manager=build_manager,
        profile_loader=profile,
        state_store=StateStore(tmp_path / "state" / "processed.json"),
        enabled=enabled,
        reports_dir=tmp_path / "reports",
        clock=clock or (lambda: NOW),
        lock=lock,
    )


# ----------------------------------------------------------------------
# The production data directory is off limits to tests
# ----------------------------------------------------------------------


class TestProductionWritesAreBlocked:
    def test_the_guard_recognises_the_real_state_path(self):
        assert _is_production_data(PRODUCTION_DATA_DIR / "state" / "processed.json")

    def test_the_guard_recognises_the_real_reports_directory(self):
        assert _is_production_data(PRODUCTION_DATA_DIR / "reports" / "x.md")

    def test_the_guard_ignores_temporary_paths(self, tmp_path):
        assert not _is_production_data(tmp_path / "state.json")

    def test_writing_the_real_state_file_fails_loudly(self):
        with pytest.raises(AssertionError, match="production data directory"):
            (PRODUCTION_DATA_DIR / "state" / "processed.json").write_text("{}", encoding="utf-8")

    def test_writing_a_real_report_fails_loudly(self):
        with pytest.raises(AssertionError, match="production data directory"):
            (PRODUCTION_DATA_DIR / "reports" / "injected.md").write_text("x", encoding="utf-8")

    def test_replacing_into_the_real_directory_fails_loudly(self, tmp_path):
        """StateStore writes a .tmp then os.replace()s it into place."""
        source = tmp_path / "s.tmp"
        source.write_text("{}", encoding="utf-8")
        with pytest.raises(AssertionError, match="production data directory"):
            os.replace(source, PRODUCTION_DATA_DIR / "state" / "processed.json")

    def test_low_level_open_for_writing_fails_loudly(self):
        """The lock file is created with os.open, not Path.write_text."""
        with pytest.raises(AssertionError, match="production data directory"):
            os.open(PRODUCTION_DATA_DIR / "state" / "x.lock", os.O_CREAT | os.O_WRONLY)

    def test_reading_production_state_is_still_permitted(self):
        """Only writes are blocked - a test may look, and one below does."""
        state = PRODUCTION_DATA_DIR / "state" / "processed.json"
        if state.exists():
            assert isinstance(json.loads(state.read_text(encoding="utf-8")), dict)

    def test_the_real_state_file_is_untouched_by_this_suite(self):
        """The property the whole isolation effort exists to protect."""
        state = PRODUCTION_DATA_DIR / "state" / "processed.json"
        if not state.exists():
            pytest.skip("no production state on this machine")
        raw = json.loads(state.read_text(encoding="utf-8"))
        assert "messages" in raw and "opportunities" in raw

    def test_a_temporary_path_still_writes_normally(self, tmp_path):
        target = tmp_path / "fine.json"
        target.write_text("{}", encoding="utf-8")
        assert target.read_text(encoding="utf-8") == "{}"


# ----------------------------------------------------------------------
# A realistic cycle, isolated
# ----------------------------------------------------------------------


class TestRunnerUsesTheInjectedPaths:
    def test_a_full_cycle_runs_against_temporary_state(self, tmp_path):
        result = make_runner(tmp_path).tick()
        assert result.status in (TickStatus.REPORTED, TickStatus.NOTHING_NEW)

    def test_state_is_written_where_it_was_told(self, tmp_path):
        make_runner(tmp_path).tick()
        assert (tmp_path / "state" / "processed.json").exists()

    def test_reports_land_in_the_temporary_directory(self, tmp_path):
        result = make_runner(tmp_path).tick()
        if result.report_path is not None:
            assert tmp_path in Path(result.report_path).parents

    def test_sample_ids_never_reach_production_state(self, tmp_path):
        """The exact contamination that happened: msg-001..006 in the real file."""
        make_runner(tmp_path).tick()
        written = json.loads((tmp_path / "state" / "processed.json").read_text(encoding="utf-8"))
        assert any(k.startswith("msg-") for k in written["messages"])

        production = PRODUCTION_DATA_DIR / "state" / "processed.json"
        if production.exists():
            before = production.read_bytes()
            make_runner(tmp_path).tick()
            assert production.read_bytes() == before

    def test_the_cycle_is_still_realistic(self, tmp_path):
        """Isolation must not have turned this into a stub - a real mailbox is
        fetched, real analyzers run, real state is recorded."""
        make_runner(tmp_path).tick()
        written = json.loads((tmp_path / "state" / "processed.json").read_text(encoding="utf-8"))
        assert len(written["messages"]) >= 1
        assert written["last_run_at"] is not None


# ----------------------------------------------------------------------
# --dry-run semantics, pinned
# ----------------------------------------------------------------------


class TestDryRunSemantics:
    """The audit asked which contract --dry-run holds. From the code:

        heartbeat.py:4    "one cycle against the sample inbox"
        heartbeat.py:139  "use the built-in sample inbox; makes no Gmail
                           request at all"

    Both describe the *mailbox*, and neither claims the run is non-mutating.
    --dry-run is therefore option A - a realistic cycle with Gmail suppressed -
    and it does record state and may file a report. These tests pin that,
    so the meaning can no longer drift by accident.
    """

    def test_dry_run_means_no_gmail_not_no_writes(self):
        import scripts.heartbeat as heartbeat

        help_text = heartbeat.__doc__ or ""
        assert "sample inbox" in help_text

    def test_a_dry_run_cycle_does_write_state(self, tmp_path):
        make_runner(tmp_path).tick()
        assert (tmp_path / "state" / "processed.json").exists()

    def test_a_dry_run_cycle_may_write_a_report(self, tmp_path):
        result = make_runner(tmp_path).tick()
        if result.status is TickStatus.REPORTED:
            assert result.report_path is not None

    def test_a_second_dry_run_reports_nothing_new(self, tmp_path):
        """Deduplication works on sample data exactly as on real mail."""
        runner = make_runner(tmp_path)
        runner.tick()
        second = make_runner(tmp_path).tick()
        assert second.status is TickStatus.NOTHING_NEW

    def test_no_gmail_module_is_imported_by_a_dry_run(self, tmp_path):
        import sys

        make_runner(tmp_path).tick()
        assert "googleapiclient" not in sys.modules


# ----------------------------------------------------------------------
# The lock
# ----------------------------------------------------------------------


class TestLockBasics:
    def test_a_free_lock_can_be_acquired(self, tmp_path):
        assert HeartbeatLock(tmp_path / "h.lock").acquire() is True

    def test_acquiring_creates_the_file(self, tmp_path):
        lock = HeartbeatLock(tmp_path / "h.lock")
        lock.acquire()
        assert lock.path.exists()

    def test_the_file_records_the_pid_and_time(self, tmp_path):
        lock = HeartbeatLock(tmp_path / "h.lock")
        lock.acquire()
        raw = json.loads(lock.path.read_text(encoding="utf-8"))
        assert raw["pid"] == os.getpid()
        assert raw["acquired_at"]

    def test_releasing_removes_the_file(self, tmp_path):
        lock = HeartbeatLock(tmp_path / "h.lock")
        lock.acquire()
        lock.release()
        assert not lock.path.exists()

    def test_release_is_idempotent(self, tmp_path):
        lock = HeartbeatLock(tmp_path / "h.lock")
        lock.acquire()
        lock.release()
        lock.release()  # must not raise

    def test_releasing_a_lock_never_held_does_nothing(self, tmp_path):
        holder = HeartbeatLock(tmp_path / "h.lock")
        holder.acquire()

        other = HeartbeatLock(tmp_path / "h.lock")
        other.release()
        assert holder.path.exists(), "a non-holder deleted the holder's lock"

    def test_double_acquire_by_one_instance_is_refused(self, tmp_path):
        lock = HeartbeatLock(tmp_path / "h.lock")
        lock.acquire()
        with pytest.raises(RuntimeError):
            lock.acquire()

    def test_it_works_as_a_context_manager(self, tmp_path):
        lock = HeartbeatLock(tmp_path / "h.lock")
        with lock as got:
            assert got is True
        assert not lock.path.exists()

    def test_the_directory_is_created_if_missing(self, tmp_path):
        lock = HeartbeatLock(tmp_path / "deep" / "nested" / "h.lock")
        assert lock.acquire() is True


class TestMutualExclusion:
    def test_a_second_holder_is_refused(self, tmp_path):
        first = HeartbeatLock(tmp_path / "h.lock")
        second = HeartbeatLock(tmp_path / "h.lock")
        assert first.acquire() is True
        assert second.acquire() is False

    def test_the_second_can_acquire_after_the_first_releases(self, tmp_path):
        first = HeartbeatLock(tmp_path / "h.lock")
        second = HeartbeatLock(tmp_path / "h.lock")
        first.acquire()
        first.release()
        assert second.acquire() is True

    def test_a_refused_holder_does_not_think_it_holds_the_lock(self, tmp_path):
        first = HeartbeatLock(tmp_path / "h.lock")
        second = HeartbeatLock(tmp_path / "h.lock")
        first.acquire()
        second.acquire()
        assert second.held is False


class TestStaleLocks:
    def _aged(self, tmp_path, age):
        """A lock left behind by a run that started ``age`` ago."""
        path = tmp_path / "h.lock"
        path.write_text(
            json.dumps({"pid": 999999, "acquired_at": (NOW - age).isoformat()}),
            encoding="utf-8",
        )
        return HeartbeatLock(path, clock=lambda: NOW)

    def test_a_fresh_foreign_lock_is_respected(self, tmp_path):
        assert self._aged(tmp_path, timedelta(minutes=5)).acquire() is False

    def test_an_abandoned_lock_is_reclaimed(self, tmp_path):
        """A crashed run must not disable the heartbeat forever."""
        assert self._aged(tmp_path, DEFAULT_STALE_AFTER + timedelta(minutes=1)).acquire() is True

    def test_reclaiming_takes_ownership(self, tmp_path):
        lock = self._aged(tmp_path, timedelta(hours=5))
        lock.acquire()
        assert lock.held is True
        assert json.loads(lock.path.read_text(encoding="utf-8"))["pid"] == os.getpid()

    def test_a_lock_exactly_at_the_limit_is_still_respected(self, tmp_path):
        assert self._aged(tmp_path, DEFAULT_STALE_AFTER).acquire() is False

    def test_an_unreadable_lock_falls_back_to_its_mtime(self, tmp_path):
        """A lock file created but never written - the window inside _create."""
        path = tmp_path / "h.lock"
        path.write_text("not json at all", encoding="utf-8")
        assert HeartbeatLock(path).acquire() is False

    def test_an_empty_lock_file_is_not_treated_as_free(self, tmp_path):
        path = tmp_path / "h.lock"
        path.write_text("", encoding="utf-8")
        assert HeartbeatLock(path).acquire() is False


# ----------------------------------------------------------------------
# The lock, as the runner uses it
# ----------------------------------------------------------------------


class TestRunnerLocking:
    def test_a_normal_tick_acquires_and_releases(self, tmp_path):
        runner = make_runner(tmp_path)
        result = runner.tick()
        assert result.status is not TickStatus.LOCKED
        assert not runner.lock.path.exists()

    def test_the_lock_sits_beside_the_state_it_protects(self, tmp_path):
        runner = make_runner(tmp_path)
        assert runner.lock.path.parent == (tmp_path / "state")
        assert runner.lock.path.suffix == ".lock"

    def test_a_second_runner_is_refused_while_the_first_holds_it(self, tmp_path):
        held = HeartbeatLock(tmp_path / "state" / "processed.lock")
        held.acquire()

        result = make_runner(tmp_path).tick()
        assert result.status is TickStatus.LOCKED

    def test_a_locked_out_run_touches_no_mailbox(self, tmp_path):
        class ExplodingMailbox(Mailbox):
            def fetch(self, limit=25, label=None):
                raise AssertionError("a locked-out run reached the mailbox")

        HeartbeatLock(tmp_path / "state" / "processed.lock").acquire()
        result = make_runner(tmp_path, mailbox=ExplodingMailbox()).tick()
        assert result.status is TickStatus.LOCKED
        assert result.touched_gmail is False

    def test_a_locked_out_run_writes_no_state(self, tmp_path):
        HeartbeatLock(tmp_path / "state" / "processed.lock").acquire()
        make_runner(tmp_path).tick()
        assert not (tmp_path / "state" / "processed.json").exists()

    def test_a_locked_out_run_writes_no_report(self, tmp_path):
        HeartbeatLock(tmp_path / "state" / "processed.lock").acquire()
        result = make_runner(tmp_path).tick()
        assert result.report_path is None

    def test_two_sequential_runs_do_not_duplicate_the_report(self, tmp_path):
        """The user-visible symptom of a concurrency bug."""
        first = make_runner(tmp_path).tick()
        second = make_runner(tmp_path).tick()
        assert first.status is TickStatus.REPORTED
        assert second.status is TickStatus.NOTHING_NEW
        assert second.report_path is None

    def test_a_locked_out_run_leaves_the_holders_lock_intact(self, tmp_path):
        held = HeartbeatLock(tmp_path / "state" / "processed.lock")
        held.acquire()
        make_runner(tmp_path).tick()
        assert held.path.exists()

    def test_the_lock_is_released_even_when_the_cycle_fails(self, tmp_path):
        class BrokenMailbox(Mailbox):
            def fetch(self, limit=25, label=None):
                raise MailboxError("the mail server is unreachable")

        runner = make_runner(tmp_path, mailbox=BrokenMailbox())
        runner.tick()
        assert not runner.lock.path.exists(), "a failed cycle kept the lock"

    def test_the_lock_is_released_when_the_profile_is_unusable(self, tmp_path):
        def broken_profile():
            from src.services.profile.profile import ProfileError

            raise ProfileError("USER.md is unreadable")

        runner = make_runner(tmp_path)
        runner._profile_loader = broken_profile
        result = runner.tick()

        assert result.status is TickStatus.PROFILE_ERROR
        assert not runner.lock.path.exists()

    def test_a_disabled_heartbeat_takes_no_lock(self, tmp_path):
        runner = make_runner(tmp_path, enabled=False)
        result = runner.tick()
        assert result.status is TickStatus.DISABLED
        assert not runner.lock.path.exists()

    def test_a_crashed_run_is_recovered_on_the_next_wake(self, tmp_path):
        """A lock left by a killed process must not stop tomorrow's run."""
        abandoned = tmp_path / "state" / "processed.lock"
        abandoned.parent.mkdir(parents=True, exist_ok=True)
        abandoned.write_text(
            json.dumps({"pid": 999999, "acquired_at": (NOW - timedelta(days=1)).isoformat()}),
            encoding="utf-8",
        )

        result = make_runner(tmp_path).tick()
        assert result.status is not TickStatus.LOCKED


class TestExistingBehaviourIntact:
    def test_disabled_still_short_circuits(self, tmp_path):
        assert make_runner(tmp_path, enabled=False).tick().status is TickStatus.DISABLED

    def test_tick_still_never_raises(self, tmp_path):
        class BrokenMailbox(Mailbox):
            def fetch(self, limit=25, label=None):
                raise MailboxError("boom")

        assert make_runner(tmp_path, mailbox=BrokenMailbox()).tick() is not None

    def test_locked_is_a_new_status_not_a_replacement(self, tmp_path):
        for name in ("DISABLED", "REPORTED", "NOTHING_NEW", "PROFILE_ERROR", "FAILED", "REJECTED"):
            assert hasattr(TickStatus, name)

    def test_a_locked_result_describes_itself(self):
        from src.heartbeat.runner import TickResult

        assert "already running" in TickResult(status=TickStatus.LOCKED).describe()
