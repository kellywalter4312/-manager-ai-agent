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

    @property
    def touched_gmail(self) -> bool:
        """Whether this wake could have reached the mailbox at all."""
        return self.status not in (TickStatus.DISABLED, TickStatus.PROFILE_ERROR)

    def describe(self) -> str:
        if self.status is TickStatus.DISABLED:
            return "heartbeat disabled; no mailbox access attempted"
        if self.status is TickStatus.PROFILE_ERROR:
            return f"profile unusable, stopped before any mailbox access: {self.error}"
        if self.status is TickStatus.REPORTED:
            return (
                f"{self.new_opportunities} new opportunity(ies) "
                f"({self.already_seen} already seen) -> {self.report_path}"
            )
        if self.status is TickStatus.NOTHING_NEW:
            return f"nothing new ({self.already_seen} already seen); no report written"
        return f"{self.status.value}: {self.error}"


class HeartbeatRunner:
    """Performs one heartbeat cycle per call."""

    def __init__(
        self,
        build_manager: Callable[[UserProfile], ManagerAgent],
        profile_loader: Callable[[], UserProfile],
        state_store: StateStore,
        policy: BehaviourPolicy | None = None,
        enabled: bool = False,
        fetch_limit: int = 25,
        reports_dir: str | Path = DEFAULT_REPORTS_DIR,
        clock: Callable[[], datetime] = utc_now,
        report_writer: Callable[[str, datetime, Any], Path] = write_report,
    ) -> None:
        """The Manager is built per tick, not held.

        USER.md must be re-read on every wake so an edit takes effect without a
        restart - and the profile is baked into the analyzer at construction
        time. Rebuilding through an injected factory keeps that promise honest
        while leaving this class ignorant of how agents are assembled.
        """
        self._build_manager = build_manager
        self._profile_loader = profile_loader
        self._state_store = state_store
        self._policy = policy or BehaviourPolicy()
        self._enabled = enabled
        self._fetch_limit = fetch_limit
        self._reports_dir = reports_dir
        self._clock = clock
        self._report_writer = report_writer

    @property
    def enabled(self) -> bool:
        return self._enabled

    def tick(self) -> TickResult:
        """One wake. Never raises: a heartbeat that crashes stops beating."""
        if not self._enabled:
            logger.info("heartbeat disabled; skipping wake")
            return TickResult(status=TickStatus.DISABLED)

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
        manager = self._build_manager(profile)

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

        if reportable:
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
            for opportunity in reportable:
                state.remember_opportunity(
                    fingerprint(opportunity), _headline_of(opportunity), now
                )
        elif self._policy.report_when_nothing_new:
            text = render_report(
                opportunities=(), skipped=already_seen, considered=considered, when=now
            )
            report_path = self._report_writer(text, now, self._reports_dir)

        self._state_store.save(state, now)

        status = TickStatus.REPORTED if reportable else TickStatus.NOTHING_NEW
        tick = TickResult(
            status=status,
            new_opportunities=len(fresh),
            already_seen=already_seen,
            considered=considered,
            report_path=report_path,
            outcome=outcome,
            details=tuple(_headline_of(o) for o in fresh),
        )
        logger.info("heartbeat: %s", tick.describe())
        return tick


def _headline_of(opportunity: Any) -> str:
    headline = getattr(opportunity, "headline", None)
    return headline() if callable(headline) else str(opportunity)
