"""Tests for what happens when a heartbeat cannot write down what it found.

The audit found that report writing and state saving could both raise straight
out of _handle_success. A cycle that read Gmail successfully and analysed it
correctly would then end in an exception, leaving the caller unable to say
whether anything had been recorded.

The ordering under test, and the reason for it:

    write the report  ->  mark those opportunities reported  ->  save state

Reversed, a failure between "marked reported" and "report written" would leave
the state claiming the user had been told about roles that exist in no file -
and because they are then deduplicated, they would never be reported again.
Report-first means the worst case is hearing something twice.

So the two failures degrade differently, and deliberately:

    report fails   nothing is recorded at all; the next wake redoes the cycle
    state fails    the report stands; the next wake may repeat it

Both are at-least-once. Neither can lose a finding, and neither can claim a
report that was never written.

Temporary paths throughout - no Gmail, no model, no network, no production data.
"""

import json
from datetime import datetime, timedelta, timezone

import pytest

from src.agents.email_agent import EmailAgent
from src.agents.email.analyzers import JobOpportunityAnalyzer
from src.heartbeat.lock import HeartbeatLock
from src.heartbeat.runner import HeartbeatRunner, TickResult, TickStatus
from src.heartbeat.state import StateStore
from src.manager.manager import ManagerAgent
from src.services.email.sample_inbox import sample_inbox
from src.services.identity.soul import BehaviourPolicy
from src.services.profile.loader import load_profile

NOW = datetime(2026, 8, 21, 10, 0, tzinfo=timezone.utc)


class ReportWriterExploded(RuntimeError):
    """Raised by a report writer that cannot persist."""


class StateSaveExploded(OSError):
    """Raised by a state store that cannot persist."""


def profile():
    return load_profile("USER.example.md")


class FailingStateStore(StateStore):
    """A real store that refuses to save. load() still works."""

    def __init__(self, path, fail_on_save=True):
        super().__init__(path)
        self.fail_on_save = fail_on_save
        self.saves = 0

    def save(self, state, now):
        self.saves += 1
        if self.fail_on_save:
            raise StateSaveExploded("the disk is full")
        return super().save(state, now)


def make_runner(
    tmp_path,
    *,
    report_writer=None,
    state_store=None,
    policy=None,
    clock=None,
    enabled=True,
):
    """A real runner on temporary paths, with one collaborator swappable."""
    def build_manager(user_profile):
        manager = ManagerAgent()
        manager.register_agent(
            EmailAgent(sample_inbox(), analyzers=[JobOpportunityAnalyzer(profile=user_profile)])
        )
        return manager

    def default_writer(text, when, directory):
        from src.heartbeat.reporting import write_report

        return write_report(text, when, directory)

    return HeartbeatRunner(
        build_manager=build_manager,
        profile_loader=profile,
        state_store=state_store or StateStore(tmp_path / "state" / "processed.json"),
        policy=policy or BehaviourPolicy(),
        enabled=enabled,
        reports_dir=tmp_path / "reports",
        clock=clock or (lambda: NOW),
        report_writer=report_writer or default_writer,
    )


def exploding_writer(text, when, directory):
    raise ReportWriterExploded("the reports volume is read-only")


def state_file(tmp_path):
    return tmp_path / "state" / "processed.json"


# ----------------------------------------------------------------------
# The happy path is unchanged
# ----------------------------------------------------------------------


class TestSuccessIsUnchanged:
    def test_a_normal_cycle_still_reports(self, tmp_path):
        result = make_runner(tmp_path).tick()
        assert result.status is TickStatus.REPORTED
        assert result.new_opportunities > 0

    def test_it_writes_a_report_and_says_so(self, tmp_path):
        result = make_runner(tmp_path).tick()
        assert result.report_path is not None
        assert result.report_persisted is True

    def test_it_saves_state_and_says_so(self, tmp_path):
        result = make_runner(tmp_path).tick()
        assert result.state_persisted is True
        assert state_file(tmp_path).exists()

    def test_deduplication_still_works(self, tmp_path):
        make_runner(tmp_path).tick()
        second = make_runner(tmp_path).tick()
        assert second.status is TickStatus.NOTHING_NEW

    def test_a_nothing_new_cycle_still_persists_state(self, tmp_path):
        make_runner(tmp_path).tick()
        second = make_runner(tmp_path).tick()
        assert second.state_persisted is True
        assert second.report_persisted is False

    def test_report_naming_is_untouched(self, tmp_path):
        result = make_runner(tmp_path).tick()
        assert result.report_path.name.endswith("-opportunities.md")


# ----------------------------------------------------------------------
# Report persistence failure
# ----------------------------------------------------------------------


class TestReportFailure:
    def test_the_tick_does_not_raise(self, tmp_path):
        """A cycle that read the mailbox successfully must not end in a
        traceback the caller has to interpret."""
        result = make_runner(tmp_path, report_writer=exploding_writer).tick()
        assert isinstance(result, TickResult)

    def test_the_status_says_the_report_failed(self, tmp_path):
        result = make_runner(tmp_path, report_writer=exploding_writer).tick()
        assert result.status is TickStatus.REPORT_FAILED

    def test_it_is_not_reported_as_success(self, tmp_path):
        result = make_runner(tmp_path, report_writer=exploding_writer).tick()
        assert result.status is not TickStatus.REPORTED
        assert result.status is not TickStatus.NOTHING_NEW

    def test_nothing_claims_to_have_been_persisted(self, tmp_path):
        result = make_runner(tmp_path, report_writer=exploding_writer).tick()
        assert result.report_persisted is False
        assert result.state_persisted is False
        assert result.report_path is None

    def test_the_error_names_the_cause(self, tmp_path):
        result = make_runner(tmp_path, report_writer=exploding_writer).tick()
        assert "ReportWriterExploded" in result.error
        assert "read-only" in result.error

    def test_what_was_found_is_still_reported_in_the_result(self, tmp_path):
        """The findings are real even though they could not be written down -
        a caller deciding whether to alert someone needs to know that."""
        result = make_runner(tmp_path, report_writer=exploding_writer).tick()
        assert result.new_opportunities > 0
        assert result.details

    def test_no_state_file_is_written(self, tmp_path):
        """The critical one. Saving the message ids here would exclude those
        messages from the next fetch, and the finding would be lost forever."""
        make_runner(tmp_path, report_writer=exploding_writer).tick()
        assert not state_file(tmp_path).exists()

    def test_existing_state_is_left_exactly_as_it_was(self, tmp_path):
        good = make_runner(tmp_path).tick()
        assert good.state_persisted is True
        before = state_file(tmp_path).read_bytes()

        make_runner(tmp_path, report_writer=exploding_writer).tick()
        assert state_file(tmp_path).read_bytes() == before

    def test_it_is_logged_with_a_traceback(self, tmp_path, caplog):
        import logging

        with caplog.at_level(logging.ERROR, logger="src"):
            make_runner(tmp_path, report_writer=exploding_writer).tick()
        assert "ReportWriterExploded" in caplog.text

    def test_the_description_explains_the_consequence(self, tmp_path):
        result = make_runner(tmp_path, report_writer=exploding_writer).tick()
        assert "examine them again" in result.describe()

    def test_an_unwritable_report_directory_behaves_the_same(self, tmp_path):
        """Not a synthetic exception: a real directory problem."""
        def writer_into_a_file(text, when, directory):
            from src.heartbeat.reporting import write_report

            blocker = tmp_path / "blocked"
            blocker.write_text("I am a file, not a directory", encoding="utf-8")
            return write_report(text, when, blocker / "reports")

        result = make_runner(tmp_path, report_writer=writer_into_a_file).tick()
        assert result.status is TickStatus.REPORT_FAILED
        assert result.state_persisted is False

    def test_a_nothing_new_report_failure_is_also_explicit(self, tmp_path):
        """report_when_nothing_new means a quiet run writes a file too."""
        policy = BehaviourPolicy(report_when_nothing_new=True)
        make_runner(tmp_path, policy=policy).tick()

        second = make_runner(tmp_path, policy=policy, report_writer=exploding_writer).tick()
        assert second.status is TickStatus.REPORT_FAILED
        assert second.state_persisted is False


# ----------------------------------------------------------------------
# State persistence failure
# ----------------------------------------------------------------------


class TestStateFailure:
    def test_the_tick_does_not_raise(self, tmp_path):
        store = FailingStateStore(state_file(tmp_path))
        assert isinstance(make_runner(tmp_path, state_store=store).tick(), TickResult)

    def test_the_status_says_the_state_failed(self, tmp_path):
        store = FailingStateStore(state_file(tmp_path))
        result = make_runner(tmp_path, state_store=store).tick()
        assert result.status is TickStatus.STATE_FAILED

    def test_it_is_not_reported_as_success(self, tmp_path):
        store = FailingStateStore(state_file(tmp_path))
        result = make_runner(tmp_path, state_store=store).tick()
        assert result.status is not TickStatus.REPORTED

    def test_the_report_is_still_acknowledged(self, tmp_path):
        """It genuinely was written - denying that would be its own lie."""
        store = FailingStateStore(state_file(tmp_path))
        result = make_runner(tmp_path, state_store=store).tick()
        assert result.report_persisted is True
        assert result.report_path is not None
        assert result.report_path.exists()

    def test_state_persistence_is_denied(self, tmp_path):
        store = FailingStateStore(state_file(tmp_path))
        result = make_runner(tmp_path, state_store=store).tick()
        assert result.state_persisted is False

    def test_the_error_names_the_cause(self, tmp_path):
        store = FailingStateStore(state_file(tmp_path))
        result = make_runner(tmp_path, state_store=store).tick()
        assert "StateSaveExploded" in result.error
        assert "disk is full" in result.error

    def test_no_state_file_is_created(self, tmp_path):
        store = FailingStateStore(state_file(tmp_path))
        make_runner(tmp_path, state_store=store).tick()
        assert not state_file(tmp_path).exists()

    def test_pre_existing_state_survives_intact(self, tmp_path):
        """A failed save must not corrupt what was already there."""
        make_runner(tmp_path).tick()
        before = state_file(tmp_path).read_bytes()

        store = FailingStateStore(state_file(tmp_path))
        make_runner(tmp_path, state_store=store).tick()
        assert state_file(tmp_path).read_bytes() == before

    def test_the_surviving_state_is_still_valid_json(self, tmp_path):
        make_runner(tmp_path).tick()
        store = FailingStateStore(state_file(tmp_path))
        make_runner(tmp_path, state_store=store).tick()

        raw = json.loads(state_file(tmp_path).read_text(encoding="utf-8"))
        assert "messages" in raw and "opportunities" in raw

    def test_no_partial_file_is_left_behind(self, tmp_path):
        """Atomicity: the temporary file must never become the real one."""
        store = FailingStateStore(state_file(tmp_path))
        make_runner(tmp_path, state_store=store).tick()
        leftovers = list((tmp_path / "state").glob("*.json")) if (tmp_path / "state").exists() else []
        assert leftovers == []

    def test_a_failure_during_the_atomic_move_leaves_the_old_state_intact(
        self, tmp_path, monkeypatch
    ):
        """The real atomicity case, not a store that refuses up front.

        StateStore writes a .tmp and then os.replace()s it into position. This
        fails the replace itself - the moment where a non-atomic implementation
        would leave a half-written processed.json behind.
        """
        import os as os_module

        good = make_runner(tmp_path).tick()
        assert good.state_persisted is True
        before = state_file(tmp_path).read_bytes()

        real_replace = os_module.replace

        def failing_replace(src, dst, *args, **kwargs):
            if str(dst).endswith("processed.json"):
                raise StateSaveExploded("the volume disappeared mid-write")
            return real_replace(src, dst, *args, **kwargs)

        monkeypatch.setattr(os_module, "replace", failing_replace)
        result = make_runner(tmp_path).tick()

        assert result.status is TickStatus.STATE_FAILED
        assert state_file(tmp_path).read_bytes() == before, "the old state was damaged"
        assert json.loads(state_file(tmp_path).read_text(encoding="utf-8"))["messages"]

    def test_a_failed_move_does_not_publish_the_temporary_file(self, tmp_path, monkeypatch):
        """Whatever the .tmp contained must not become the live state."""
        import os as os_module

        make_runner(tmp_path).tick()
        before = json.loads(state_file(tmp_path).read_text(encoding="utf-8"))

        real_replace = os_module.replace

        def failing_replace(src, dst, *args, **kwargs):
            if str(dst).endswith("processed.json"):
                raise StateSaveExploded("interrupted")
            return real_replace(src, dst, *args, **kwargs)

        monkeypatch.setattr(os_module, "replace", failing_replace)
        make_runner(tmp_path).tick()

        after = json.loads(state_file(tmp_path).read_text(encoding="utf-8"))
        assert after["last_run_at"] == before["last_run_at"], "a failed save advanced last_run_at"

    def test_it_is_logged_with_a_traceback(self, tmp_path, caplog):
        import logging

        store = FailingStateStore(state_file(tmp_path))
        with caplog.at_level(logging.ERROR, logger="src"):
            make_runner(tmp_path, state_store=store).tick()
        assert "StateSaveExploded" in caplog.text

    def test_the_description_explains_the_consequence(self, tmp_path):
        store = FailingStateStore(state_file(tmp_path))
        result = make_runner(tmp_path, state_store=store).tick()
        assert "may repeat the report" in result.describe()


# ----------------------------------------------------------------------
# The ordering invariant
# ----------------------------------------------------------------------


class TestOrderingInvariant:
    def test_nothing_is_marked_reported_when_the_report_failed(self, tmp_path):
        """The invariant, asserted on the state itself: after a failed report
        there is no record claiming those opportunities were reported."""
        make_runner(tmp_path, report_writer=exploding_writer).tick()
        assert not state_file(tmp_path).exists()

    def test_a_report_precedes_the_state_that_records_it(self, tmp_path):
        """Proven by observation: when the state save runs, the report file
        already exists on disk."""
        seen = {}

        class ObservingStore(StateStore):
            def save(self, state, now):
                seen["report_existed"] = any((tmp_path / "reports").glob("*.md"))
                seen["opportunities"] = len(state.opportunities)
                return super().save(state, now)

        make_runner(tmp_path, state_store=ObservingStore(state_file(tmp_path))).tick()
        assert seen["report_existed"] is True
        assert seen["opportunities"] > 0

    def test_a_failed_report_never_reaches_the_state_store(self, tmp_path):
        store = FailingStateStore(state_file(tmp_path), fail_on_save=False)
        make_runner(tmp_path, state_store=store, report_writer=exploding_writer).tick()
        assert store.saves == 0, "state was saved despite the report failing"


# ----------------------------------------------------------------------
# Combined failure
# ----------------------------------------------------------------------


class TestCombinedFailure:
    def test_report_failure_wins_because_it_happens_first(self, tmp_path):
        store = FailingStateStore(state_file(tmp_path))
        result = make_runner(tmp_path, state_store=store, report_writer=exploding_writer).tick()
        assert result.status is TickStatus.REPORT_FAILED

    def test_neither_is_claimed_as_persisted(self, tmp_path):
        store = FailingStateStore(state_file(tmp_path))
        result = make_runner(tmp_path, state_store=store, report_writer=exploding_writer).tick()
        assert result.report_persisted is False
        assert result.state_persisted is False

    def test_no_state_file_results(self, tmp_path):
        store = FailingStateStore(state_file(tmp_path))
        make_runner(tmp_path, state_store=store, report_writer=exploding_writer).tick()
        assert not state_file(tmp_path).exists()

    def test_it_is_deterministic_across_repeats(self, tmp_path):
        store = FailingStateStore(state_file(tmp_path))
        statuses = {
            make_runner(tmp_path, state_store=store, report_writer=exploding_writer).tick().status
            for _ in range(3)
        }
        assert statuses == {TickStatus.REPORT_FAILED}


# ----------------------------------------------------------------------
# What the next heartbeat does
# ----------------------------------------------------------------------


class TestTheNextHeartbeat:
    def test_after_a_report_failure_the_work_is_redone(self, tmp_path):
        first = make_runner(tmp_path, report_writer=exploding_writer).tick()
        assert first.status is TickStatus.REPORT_FAILED

        second = make_runner(tmp_path).tick()
        assert second.status is TickStatus.REPORTED
        assert second.new_opportunities == first.new_opportunities

    def test_after_a_report_failure_nothing_was_lost(self, tmp_path):
        """The property that justifies discarding the whole cycle."""
        clean = make_runner(tmp_path / "clean").tick()

        make_runner(tmp_path, report_writer=exploding_writer).tick()
        recovered = make_runner(tmp_path).tick()
        assert recovered.new_opportunities == clean.new_opportunities

    def test_after_a_state_failure_the_report_may_repeat(self, tmp_path):
        """At-least-once, stated plainly: the user may hear it twice, and the
        test says so rather than leaving it to be discovered."""
        store = FailingStateStore(state_file(tmp_path))
        first = make_runner(tmp_path, state_store=store).tick()
        assert first.status is TickStatus.STATE_FAILED

        second = make_runner(tmp_path).tick()
        assert second.status is TickStatus.REPORTED
        assert second.new_opportunities == first.new_opportunities

    def test_a_recovered_cycle_then_deduplicates_normally(self, tmp_path):
        make_runner(tmp_path, report_writer=exploding_writer).tick()
        make_runner(tmp_path).tick()
        third = make_runner(tmp_path).tick()
        assert third.status is TickStatus.NOTHING_NEW

    def test_transient_failure_then_success_leaves_correct_state(self, tmp_path):
        store = FailingStateStore(state_file(tmp_path))
        make_runner(tmp_path, state_store=store).tick()

        store.fail_on_save = False
        recovered = make_runner(tmp_path, state_store=store).tick()
        assert recovered.state_persisted is True

        raw = json.loads(state_file(tmp_path).read_text(encoding="utf-8"))
        assert len(raw["messages"]) > 0
        assert len(raw["opportunities"]) > 0


# ----------------------------------------------------------------------
# The lock is unaffected
# ----------------------------------------------------------------------


class TestConcurrencyIsNotWeakened:
    def test_the_lock_is_released_after_a_report_failure(self, tmp_path):
        runner = make_runner(tmp_path, report_writer=exploding_writer)
        runner.tick()
        assert not runner.lock.path.exists()

    def test_the_lock_is_released_after_a_state_failure(self, tmp_path):
        runner = make_runner(tmp_path, state_store=FailingStateStore(state_file(tmp_path)))
        runner.tick()
        assert not runner.lock.path.exists()

    def test_the_lock_is_released_after_a_combined_failure(self, tmp_path):
        runner = make_runner(
            tmp_path,
            state_store=FailingStateStore(state_file(tmp_path)),
            report_writer=exploding_writer,
        )
        runner.tick()
        assert not runner.lock.path.exists()

    def test_another_heartbeat_can_run_after_a_failure(self, tmp_path):
        make_runner(tmp_path, report_writer=exploding_writer).tick()
        assert make_runner(tmp_path).tick().status is TickStatus.REPORTED

    def test_a_failing_cycle_cannot_hold_the_lock_permanently(self, tmp_path):
        for _ in range(3):
            make_runner(tmp_path, report_writer=exploding_writer).tick()
        assert make_runner(tmp_path).tick().status is TickStatus.REPORTED

    def test_a_held_lock_still_blocks_a_failing_runner(self, tmp_path):
        """Locking is still checked before any work, failure path or not."""
        HeartbeatLock(tmp_path / "state" / "processed.lock").acquire()
        result = make_runner(tmp_path, report_writer=exploding_writer).tick()
        assert result.status is TickStatus.LOCKED

    def test_stale_recovery_is_unchanged(self, tmp_path):
        abandoned = tmp_path / "state" / "processed.lock"
        abandoned.parent.mkdir(parents=True, exist_ok=True)
        abandoned.write_text(
            json.dumps({"pid": 999999, "acquired_at": (NOW - timedelta(days=1)).isoformat()}),
            encoding="utf-8",
        )
        assert make_runner(tmp_path).tick().status is not TickStatus.LOCKED


# ----------------------------------------------------------------------
# The status vocabulary
# ----------------------------------------------------------------------


class TestStatusVocabulary:
    def test_the_new_statuses_exist(self):
        assert TickStatus.REPORT_FAILED.value == "report_failed"
        assert TickStatus.STATE_FAILED.value == "state_failed"

    def test_the_old_statuses_are_untouched(self):
        for name in ("DISABLED", "REPORTED", "NOTHING_NEW", "PROFILE_ERROR", "FAILED",
                     "REJECTED", "LOCKED"):
            assert hasattr(TickStatus, name)

    def test_persistence_flags_default_to_false(self):
        result = TickResult(status=TickStatus.DISABLED)
        assert result.report_persisted is False
        assert result.state_persisted is False

    def test_a_failed_persistence_still_counts_as_having_touched_gmail(self):
        """It did read the mailbox - the failure came afterwards."""
        assert TickResult(status=TickStatus.REPORT_FAILED).touched_gmail is True
        assert TickResult(status=TickStatus.STATE_FAILED).touched_gmail is True

    def test_statuses_that_never_reach_the_mailbox_still_say_so(self):
        for status in (TickStatus.DISABLED, TickStatus.LOCKED, TickStatus.PROFILE_ERROR):
            assert TickResult(status=status).touched_gmail is False
