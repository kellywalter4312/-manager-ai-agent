"""The heartbeat: one wake, one mission, one decision about what to say.

    WAKE -> CHECK GMAIL -> DETECT -> ANALYSE -> MATCH -> RANK -> DEDUP -> REPORT

The runner deliberately owns none of that work. It sequences a mission and
hands it to the Manager, which decides whether to carry it out itself or
delegate to a specialist, and then judges the result. Analysis lives in the
EmailAgent, judgement lives in the Manager, and scheduling lives outside the
process entirely.

``tick()`` is a plain function call that performs exactly one cycle and
returns. There is no daemon and no internal sleep loop: an OS scheduler invokes
the entry point, the process does its work and exits. That keeps the token out
of a long-lived process, makes a crash cost one cycle rather than the schedule,
and - most usefully - makes the whole autonomous loop testable by calling a
function with a fake clock.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum
from pathlib import Path
from typing import Any, Callable

from src.core.contracts import FINDINGS_KEY, Task
from src.heartbeat.lock import HeartbeatLock
from src.heartbeat.reporting import DEFAULT_REPORTS_DIR, render_report, write_report
from src.heartbeat.state import (
    ProcessedState,
    StateStore,
    fingerprint,
    select_unseen,
    utc_now,
)
from src.manager.manager import ManagerAgent, MissionOutcome
from src.services.identity.soul import BehaviourPolicy
from src.services.profile.profile import ProfileError, UserProfile
from src.services.schedule.schedule import UserSchedule
from src.utils.logging import get_logger

logger = get_logger(__name__)

# The standing mission. Worded so the capability router sends it to the email
# specialist rather than to the Manager's own coordination skills.
MISSION = "check my gmail inbox for new job and freelance opportunities"


class TickStatus(str, Enum):
    """What became of one wake."""

    DISABLED = "disabled"          # heartbeat switched off; nothing was touched
    REPORTED = "reported"          # new opportunities found and written up
    NOTHING_NEW = "nothing_new"    # ran cleanly, nothing worth your attention
    PROFILE_ERROR = "profile_error"  # USER.md unusable; stopped before any fetch
    FAILED = "failed"              # the mission failed; will retry next wake
    REJECTED = "rejected"          # a specialist broke its contract
    LOCKED = "locked"              # another heartbeat is mid-cycle; skipped
    REPORT_FAILED = "report_failed"  # analysed fine; the report would not save
    STATE_FAILED = "state_failed"    # report saved; the state would not


@dataclass(frozen=True)
class TickResult:
    """A structured account of one wake, for logs, tests and the CLI."""

    status: TickStatus
    new_opportunities: int = 0
    already_seen: int = 0
    considered: int = 0
    report_path: Path | None = None
    outcome: MissionOutcome | None = None
    error: str | None = None
    details: tuple[str, ...] = field(default_factory=tuple)

    # Persistence is reported as two separate facts rather than inferred from
    # the status, because "we found three roles" and "three roles are written
    # down where you will see them" are different claims. Conflating them is
    # how a system ends up believing it told you something it never did.
    report_persisted: bool = False
    state_persisted: bool = False

    @property
    def touched_gmail(self) -> bool:
        """Whether this wake could have reached the mailbox at all."""
        return self.status not in (
            TickStatus.DISABLED,
            TickStatus.PROFILE_ERROR,
            TickStatus.LOCKED,
        )

    def describe(self) -> str:
        if self.status is TickStatus.DISABLED:
            return "heartbeat disabled; no mailbox access attempted"
        if self.status is TickStatus.LOCKED:
            return "another heartbeat is already running; no mailbox access attempted"
        if self.status is TickStatus.PROFILE_ERROR:
            return f"profile unusable, stopped before any mailbox access: {self.error}"
        if self.status is TickStatus.REPORTED:
            return (
                f"{self.new_opportunities} new opportunity(ies) "
                f"({self.already_seen} already seen) -> {self.report_path}"
            )
        if self.status is TickStatus.NOTHING_NEW:
            return f"nothing new ({self.already_seen} already seen); no report written"
        if self.status is TickStatus.REPORT_FAILED:
            return (
                f"analysed {self.considered} message(s) but the report could not be "
                f"written ({self.error}); nothing was recorded, so the next wake "
                "will examine them again"
            )
        if self.status is TickStatus.STATE_FAILED:
            return (
                f"report written to {self.report_path} but the state could not be "
                f"saved ({self.error}); the next wake will re-examine these "
                "messages and may repeat the report"
            )
        return f"{self.status.value}: {self.error}"


class HeartbeatRunner:
    """Performs one heartbeat cycle per call."""

    def __init__(
        self,
        build_manager: Callable[..., ManagerAgent],
        profile_loader: Callable[[], UserProfile],
        state_store: StateStore,
        schedule_loader: Callable[[], UserSchedule] | None = None,
        policy: BehaviourPolicy | None = None,
        enabled: bool = False,
        fetch_limit: int = 25,
        reports_dir: str | Path = DEFAULT_REPORTS_DIR,
        clock: Callable[[], datetime] = utc_now,
        report_writer: Callable[[str, datetime, Any], Path] = write_report,
        lock: HeartbeatLock | None = None,
    ) -> None:
        """The Manager is built per tick, not held.

        USER.md must be re-read on every wake so an edit takes effect without a
        restart - and the profile is baked into the analyzer at construction
        time. Rebuilding through an injected factory keeps that promise honest
        while leaving this class ignorant of how agents are assembled.

        ``schedule_loader`` is the same arrangement for SCHEDULE.md, and it is
        optional for a reason: a schedule is optional, so a runner built
        without one behaves exactly as every runner did before schedules
        existed. When supplied it is called once per wake, beside the profile,
        so an edit to either document takes effect on the next tick.

        The schedule is context handed onward and never read here. Nothing in
        this class inspects its contents, and nothing in it can decide whether
        a tick happens: the enabled flag and the lock do that, exactly as
        before. A document describing a person's week must never become the
        thing that starts or stops a run.
        """
        self._build_manager = build_manager
        self._profile_loader = profile_loader
        self._schedule_loader = schedule_loader
        self._state_store = state_store
        self._policy = policy or BehaviourPolicy()
        self._enabled = enabled
        self._fetch_limit = fetch_limit
        self._reports_dir = reports_dir
        self._clock = clock
        self._report_writer = report_writer

        # Defaults to a lock file beside the state it protects, so a runner
        # pointed at a temporary state path locks there too - which is what
        # keeps every test isolated without any test having to know that a
        # lock exists.
        self._lock = lock if lock is not None else HeartbeatLock(
            Path(state_store.path).with_suffix(".lock")
        )

    @property
    def enabled(self) -> bool:
        return self._enabled

    @property
    def lock(self) -> HeartbeatLock:
        return self._lock

    def tick(self) -> TickResult:
        """One wake. Never raises: a heartbeat that crashes stops beating."""
        if not self._enabled:
            logger.info("heartbeat disabled; skipping wake")
            return TickResult(status=TickStatus.DISABLED)

        # The lock covers the whole cycle, not just the write. Two runs that
        # merely wrote atomically would still both load the same state, both
        # analyse the same inbox, and the second save would drop what the first
        # had learned. Taken after the enabled check so a switched-off
        # heartbeat touches nothing at all.
        if not self._lock.acquire():
            return TickResult(status=TickStatus.LOCKED)

        try:
            return self._locked_tick()
        finally:
            # finally, not a happy path: a crash inside the cycle must not
            # leave the lock held. If the process dies outright the file
            # survives, and the stale timeout is what recovers from that.
            self._lock.release()

    def _build_manager_for(self, profile: UserProfile) -> ManagerAgent:
        """Assemble this tick's Manager, with a schedule when one is configured.

        The call shape follows the wiring rather than the other way round. A
        runner built without a schedule_loader calls the factory exactly as
        every runner has since the heartbeat existed, so a one-argument factory
        - which is what most test doubles are - keeps working untouched. Only a
        runner that was given a schedule passes one.

        A schedule that cannot be read is not a reason to skip a wake. The
        heartbeat's job is to check the inbox, and not knowing what someone's
        Tuesday looks like does not prevent that - so a failure degrades to "no
        schedule configured" and is logged rather than raised. Contrast the
        profile, which stops the tick before any mailbox access: without one
        there is nothing to match against, so the access would have no possible
        benefit.
        """
        if self._schedule_loader is None:
            return self._build_manager(profile)

        try:
            schedule = self._schedule_loader()
        except Exception as exc:  # noqa: BLE001 - deliberate boundary catch
            logger.warning(
                "could not read the schedule, continuing without it: %s", exc
            )
            schedule = UserSchedule()

        return self._build_manager(profile, schedule)

    def _locked_tick(self) -> TickResult:
        """One cycle, with the lock already held."""
        now = self._clock()

        # The profile is loaded first, on purpose. If USER.md is unusable there
        # is nothing to match against, and reaching into the mailbox anyway
        # would be an access with no possible benefit.
        try:
            profile = self._profile_loader()
        except ProfileError as exc:
            logger.error("cannot run: %s", exc)
            return TickResult(status=TickStatus.PROFILE_ERROR, error=str(exc))

        state = self._state_store.load()
        manager = self._build_manager_for(profile)

        mission = Task(
            description=MISSION,
            payload={
                "limit": self._fetch_limit,
                "exclude_message_ids": state.known_message_ids(),
            },
        )

        result = manager.handle_task(mission)
        outcome = manager.review_mission(mission, result)

        if outcome in (MissionOutcome.RETRY, MissionOutcome.FAILED):
            status = TickStatus.FAILED
            logger.warning("mission unsuccessful (%s): %s", outcome.value, result.error)
            return TickResult(status=status, outcome=outcome, error=result.error)

        if outcome is MissionOutcome.REJECTED:
            return TickResult(
                status=TickStatus.REJECTED, outcome=outcome, error=result.error
            )

        return self._handle_success(result, outcome, state, now)

    # ------------------------------------------------------------------
    # Internals
    # ------------------------------------------------------------------

    def _handle_success(self, result, outcome, state: ProcessedState, now) -> TickResult:
        payload = result.payload or {}
        found = tuple(payload.get(FINDINGS_KEY, ()) or ())
        ambiguous = tuple(payload.get("ambiguous", ()) or ())
        considered = int(payload.get("considered", 0) or 0)

        fresh, seen = select_unseen(found, state)
        fresh_ambiguous, seen_ambiguous = select_unseen(ambiguous, state)

        # Ambiguous items are deduplicated exactly like ranked ones, so the
        # ones recognised as already reported belong in the count too. Keeping
        # them out made a run that correctly suppressed a repeat look as though
        # it had seen nothing at all.
        already_seen = len(seen) + len(seen_ambiguous)

        # Every message examined is recorded, whether or not it produced an
        # opportunity - otherwise the same uninteresting mail is re-fetched and
        # re-analysed on every wake, forever.
        for message_id in payload.get("message_ids", ()) or ():
            state.remember_message(str(message_id), now)

        report_path: Path | None = None
        reportable = fresh + (fresh_ambiguous if self._policy.include_ambiguous else ())

        # The order below is the whole design, and it is deliberate:
        #
        #     write the report  ->  mark those opportunities reported  ->  save
        #
        # Reversed, a failure between "marked reported" and "report written"
        # would leave the state claiming you had been told about roles that
        # exist in no file anywhere - and because they are then deduplicated,
        # you would never be told again. Writing the report first means the
        # worst case is hearing about something twice, which is a nuisance
        # rather than a silent loss.
        if reportable:
            try:
                text = render_report(
                    opportunities=fresh,
                    ambiguous=fresh_ambiguous if self._policy.include_ambiguous else (),
                    skipped=already_seen,
                    considered=considered,
                    when=now,
                    max_reported=self._policy.max_opportunities_reported,
                    include_ambiguous=self._policy.include_ambiguous,
                )
                report_path = self._report_writer(text, now, self._reports_dir)
            except Exception as exc:  # noqa: BLE001 - deliberate boundary catch
                return self._report_failed(exc, fresh, already_seen, considered, outcome)

            for opportunity in reportable:
                state.remember_opportunity(
                    fingerprint(opportunity), _headline_of(opportunity), now
                )
        elif self._policy.report_when_nothing_new:
            try:
                text = render_report(
                    opportunities=(), skipped=already_seen, considered=considered, when=now
                )
                report_path = self._report_writer(text, now, self._reports_dir)
            except Exception as exc:  # noqa: BLE001 - deliberate boundary catch
                return self._report_failed(exc, fresh, already_seen, considered, outcome)

        try:
            self._state_store.save(state, now)
        except Exception as exc:  # noqa: BLE001 - deliberate boundary catch
            # The report is already on disk, so you have been told. What is lost
            # is only the memory of having told you: the next wake re-examines
            # these messages and may file the same report again. Duplicated
            # news, never missing news.
            logger.exception(
                "state could not be saved after a successful cycle (%s); "
                "this run's progress is not recorded",
                type(exc).__name__,
            )
            return TickResult(
                status=TickStatus.STATE_FAILED,
                new_opportunities=len(fresh),
                already_seen=already_seen,
                considered=considered,
                report_path=report_path,
                outcome=outcome,
                error=f"{type(exc).__name__}: {exc}",
                details=tuple(_headline_of(o) for o in fresh),
                report_persisted=report_path is not None,
                state_persisted=False,
            )

        status = TickStatus.REPORTED if reportable else TickStatus.NOTHING_NEW
        tick = TickResult(
            status=status,
            new_opportunities=len(fresh),
            already_seen=already_seen,
            considered=considered,
            report_path=report_path,
            outcome=outcome,
            details=tuple(_headline_of(o) for o in fresh),
            report_persisted=report_path is not None,
            state_persisted=True,
        )
        logger.info("heartbeat: %s", tick.describe())
        return tick

    def _report_failed(self, exc, fresh, already_seen, considered, outcome) -> TickResult:
        """A report that could not be written records nothing at all.

        Note what is deliberately *not* done here: the state is not saved. The
        in-memory state already carries this run's message ids, and saving them
        would exclude those messages from the next fetch - so an opportunity
        whose report failed to write would never be looked at again. Discarding
        the whole cycle costs one repeated fetch; keeping half of it costs the
        finding itself.
        """
        logger.exception(
            "report could not be written (%s); discarding this cycle's progress "
            "so the next wake re-examines the same messages",
            type(exc).__name__,
        )
        return TickResult(
            status=TickStatus.REPORT_FAILED,
            new_opportunities=len(fresh),
            already_seen=already_seen,
            considered=considered,
            report_path=None,
            outcome=outcome,
            error=f"{type(exc).__name__}: {exc}",
            details=tuple(_headline_of(o) for o in fresh),
            report_persisted=False,
            state_persisted=False,
        )


def _headline_of(opportunity: Any) -> str:
    headline = getattr(opportunity, "headline", None)
    return headline() if callable(headline) else str(opportunity)
