"""The heartbeat entry point - and the composition root for autonomous runs.

    python scripts/heartbeat.py --once      one cycle against the configured mailbox
    python scripts/heartbeat.py --dry-run   one cycle against the sample inbox
                                            (no Gmail; still records state)
    python scripts/heartbeat.py --watch     repeat on an interval, in the foreground

This script SCHEDULES NOTHING. Running it performs exactly one cycle (or, with
--watch, repeats while you watch it). To run it unattended, register --once
with your own scheduler; that is a decision for you to make deliberately, not
something a program should arrange for itself.

The heartbeat is disabled by default and does nothing until
MANAGER_HEARTBEAT_ENABLED=true. --once and --dry-run enable it for that single
invocation so a manual run is possible without turning autonomy on globally.

Read-only throughout: the Gmail token carries gmail.readonly, no Outbox
implementation exists, and nothing here can send, reply, delete, modify,
apply, bid or contact anyone.
"""

from __future__ import annotations

import argparse
import sys
import time
from datetime import datetime
from pathlib import Path
from typing import Callable

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from src.config.coherence import (  # noqa: E402
    ConfigurationError,
    check_configuration,
)
from src.config.settings import Settings  # noqa: E402
from src.composition import build_manager, build_runtime, register_defined_specialists  # noqa: E402
from src.providers.factory import ProviderConfigurationError  # noqa: E402
from src.services.memory.day import Calendar, TimezoneError  # noqa: E402
from src.heartbeat.reporting import render_proactive_report, write_report  # noqa: E402
from src.heartbeat.runner import HeartbeatRunner, TickStatus  # noqa: E402
from src.heartbeat.state import StateStore, utc_now  # noqa: E402
from src.manager.manager import ManagerAgent  # noqa: E402
from src.manager.proactive_experience import evaluate_and_persist_experiences  # noqa: E402
from src.manager.proactive_milestones import due_proactive_events  # noqa: E402
from src.providers.calendar_events_factory import build_calendar_event_provider  # noqa: E402
from src.services.credentials.account_store_file import (  # noqa: E402
    DEFAULT_ACCOUNTS_PATH,
    load_credential_store,
)
from src.services.email.mailbox_factory import build_mailbox  # noqa: E402
from src.services.email.sample_inbox import sample_inbox  # noqa: E402
from src.services.identity.soul import load_soul  # noqa: E402
from src.services.profile.loader import load_profile  # noqa: E402
from src.services.profile.profile import UserProfile  # noqa: E402
from src.services.schedule.loader import load_schedule  # noqa: E402
from src.services.schedule.schedule import UserSchedule  # noqa: E402
from src.services.evolution.governed_config import (  # noqa: E402
    DEFAULT_CONFIG_PATH,
    load_governed_config,
)
from scripts.control_center import filtered_manager_capabilities  # noqa: E402
from src.utils.logging import configure_logging  # noqa: E402


def build_manager_factory(
    settings: Settings, dry_run: bool
) -> Callable[[UserProfile, UserSchedule], ManagerAgent]:
    """A factory that assembles a fully wired Manager for a given tick.

    A factory, not an instance: USER.md and SCHEDULE.md are re-read on every
    tick and the profile is baked into the analyzer, so fresh documents mean a
    freshly assembled agent. The mailbox is built once and shared - it holds
    the lazily created API client.

    Both documents arrive as arguments rather than being read here, for the
    reason they always have: the runner owns the wake, and reading them at the
    top of the wake is what makes an edit take effect on the next tick rather
    than the next restart.
    """
    # Everything that outlives a tick, built once by the shared composition
    # root - the same one main.py uses. A provider, a router and the brains
    # hold no per-tick state, so sharing them across ticks is free.
    #
    # Universal provider/connection management repair: loads the same
    # persisted CredentialStore scripts/control_center.py::build now
    # does, for the same reason - a currently-selected account should
    # not silently apply to one entry point and not another. The
    # heartbeat grants no communication tool (see this file's own
    # "read-only throughout" docstring), so communication_account has
    # no practical effect here today; it is threaded through anyway so
    # this stays true if that ever changes, rather than silently
    # drifting from the other entry points.
    accounts = load_credential_store(DEFAULT_ACCOUNTS_PATH)
    runtime = build_runtime(
        settings,
        accounts=accounts,
        video_generation_account=accounts.active(settings.video_generation),
        speech_account=accounts.active(settings.speech),
        communication_account=accounts.active(settings.communication_provider),
    )

    # Governance-integrity repair (2026-09-08): a disabled capability was
    # silently still reachable on every heartbeat tick, because this
    # factory built a Manager without ever consulting the same
    # GovernedConfig scripts/control_center.py::build reads - the exact
    # "currently-selected account should not silently apply to one entry
    # point and not another" principle this file's own accounts= wiring
    # above already states, now honoured for capability enable/disable
    # too. Loaded once here, not per tick, matching how `accounts` itself
    # is loaded once above - a capability disabled or re-enabled while
    # this is already running takes effect on the next invocation of this
    # script, the identical restart-to-activate rule every other
    # persisted administrative change already lives under.
    capabilities = filtered_manager_capabilities(load_governed_config(DEFAULT_CONFIG_PATH))

    # --dry-run substitutes the built-in sample inbox, which is why the mailbox
    # is chosen here rather than inside the composition root. Built once and
    # shared: it holds the lazily created API client.
    mailbox = sample_inbox() if dry_run else build_mailbox(settings)

    def build(
        profile: UserProfile, schedule: UserSchedule | None = None
    ) -> ManagerAgent:
        # Built per tick, not once, and that is the point. A persistent store
        # re-reads its file at construction, so this tick sees what the last
        # one wrote even though the Manager object itself does not survive.
        # The Manager needs no lifecycle; its memory does.
        #
        # ``schedule`` is defaulted for the same reason the runner's call shape
        # follows its wiring: a caller that has no schedule - a test driving
        # this factory directly, or any code predating Step 8 - keeps working
        # unchanged. The runner supplies one on every wake.
        manager = build_manager(
            runtime,
            mailbox=mailbox,
            profile=profile,
            # Context about the person, freshly read this tick. Nothing here
            # reads it: it is handed to the Manager and never consulted to
            # decide whether this wake happens.
            schedule=schedule,
            fetch_limit=settings.heartbeat_limit,
            capabilities=capabilities,
        )
        # The owner's own specialists, registered exactly as every other
        # entry point registers them - no mission is given to them here.
        register_defined_specialists(runtime, manager, load_governed_config(DEFAULT_CONFIG_PATH).values, accounts=accounts)
        return manager

    return build


def build_runner(settings: Settings, enabled: bool, dry_run: bool) -> HeartbeatRunner:
    return HeartbeatRunner(
        build_manager=build_manager_factory(settings, dry_run),
        profile_loader=lambda: load_profile(settings.profile_path),
        # Re-read every wake, beside the profile and by the same mechanism, so
        # editing SCHEDULE.md takes effect on the next tick without a restart.
        # A missing file is not an error - it means no schedule is configured.
        schedule_loader=lambda: load_schedule(settings.schedule_path),
        state_store=StateStore(settings.state_path),
        policy=load_soul(settings.soul_path),
        enabled=enabled,
        fetch_limit=settings.heartbeat_limit,
        reports_dir=settings.reports_dir,
    )


def check_proactive_events(
    settings: Settings, dry_run: bool, *, clock: Callable[[], datetime] = utc_now,
) -> str:
    """Stage 12 closure - one proactive-event check, alongside the
    ordinary email cycle rather than inside it.

    Deliberately a second, small, additive step rather than a change to
    ``HeartbeatRunner``/``_locked_tick`` itself: that class's whole shape
    (``TickResult``, its report and its state schema) is specific to the
    email-opportunity mission, and folding an unrelated concern into it
    would risk the exact "second implementation of a shared idea" this
    project's own architecture refuses everywhere else. This reuses the
    *same* mechanisms instead - Alfred's own governed Memory and the
    calendar (through ``due_proactive_events``, which only reads), the
    same ``StateStore`` the email cycle already persists to (its
    generic ``has_opportunity``/``remember_opportunity`` bucket, keyed
    by each event's own ``dedup_key`` so nothing is reported twice for
    the same occurrence even if this runs hourly - never a second dedup
    system), and the same ``reports_dir`` (``write_report``'s
    ``suffix="proactive"`` writes beside the opportunity reports, never
    a second channel).

    **Event detection first, celebration second, exactly the brief's
    own flow.** Every due event is genuine (already governed by
    ``due_proactive_events``/``is_mentionable``) before a
    ``CelebrationReaction`` is ever selected for it - never the
    reverse, and never a reaction for something that was not actually
    due.

    No profile is loaded: a proactive event is read from the Manager's
    own Memory and the calendar, both independent of USER.md, so a
    missing or unusable profile is not a reason to skip this check the
    way it is for the email mission (which needs the profile to match
    against).
    """
    now = clock()
    # Which day this instant fell on **for the person**, not in UTC.
    #
    # This read `now.date()`, which is the UTC date, while every other
    # "what day is it" in this project goes through the configured
    # Calendar. src/services/memory/day.py states the rule this broke, in
    # the exact terms it broke it: "23:30 UTC on the 21st is already 00:30
    # on the 22nd in Lagos, and it is the *person's* day that decides what
    # 'today' means to them." So for the hour before midnight UTC, a
    # birthday that had genuinely arrived for the user was reported as not
    # due - and, worse, would then be reported the following day under a
    # dedup key naming the wrong date.
    #
    # The clock stays injected and stays authoritative: the Calendar is
    # only asked which local day the instant it returned belongs to, so a
    # test passing a fixed instant still gets a fixed, deterministic day.
    today = Calendar.configured(settings.timezone).local_date(now)

    manager = build_manager_factory(settings, dry_run)(None)
    calendar_events = build_calendar_event_provider(settings)
    due = due_proactive_events(manager, today=today, calendar_events=calendar_events, now=now)
    if not due:
        return "proactive events: none due today"

    state_store = StateStore(settings.state_path)
    state = state_store.load()

    fresh = [event for event in due if not state.has_opportunity(event.dedup_key)]
    if not fresh:
        return f"proactive events: {len(due)} due today, already reported"

    # Post-Pre-Stage-9: a due reminder also reaches the person when no page is
    # open - a desktop notification per fresh event, honouring the owner's
    # governed choice. Delivery is observed, never assumed.
    notified = _notify_desktop(settings, fresh)

    # General Time/Event Awareness + Proactive Visual Experience
    # checkpoint: the identical governed selection+persistence step a
    # live server's own snapshot read already performs
    # (evaluate_and_persist_experiences) - a real, history-aware
    # CelebrationReaction, persisted through this Manager's own governed
    # Memory (never a second selection mechanism, never only computed
    # in-process and discarded) - so whichever entry point runs first,
    # a later live session finds the identical, already-selected
    # experience waiting to be presented, rather than this process's
    # own now-forgotten in-memory choice.
    experiences = {
        record.dedup_key: {**record.to_dict(), "enabled": True}
        for record in evaluate_and_persist_experiences(
            manager, today=today, calendar_events=calendar_events, now=now,
        )
    }

    text = render_proactive_report(fresh, now, celebrations=experiences)
    path = write_report(text, now, settings.reports_dir, suffix="proactive")

    for event in fresh:
        state.remember_opportunity(event.dedup_key, event.label, now)
    state_store.save(state, now)

    note = f", {notified} shown as desktop notifications" if notified else ""
    return f"proactive events: {len(fresh)} due today -> {path}{note}"


def _notify_desktop(settings: Settings, events) -> int:
    """Show each fresh due event as a desktop notification; how many Windows
    confirmed delivered. The governed choice ('provider
    desktop_notification_provider use windows') wins over the environment."""
    from dataclasses import replace

    from src.providers.desktop_notifier import build_desktop_notifier
    from src.services.evolution.governed_config import load_governed_config

    choice = load_governed_config().get("provider.desktop_notification_provider") or settings.desktop_notifications
    try:
        notifier = build_desktop_notifier(replace(settings, desktop_notifications=choice))
    except ValueError:
        return 0
    shown = 0
    for event in events:
        # A ProactiveEvent's label is its person-facing text (a reminder's own words).
        if notifier.notify("Alfred", str(event.label), tag=str(event.dedup_key)):
            shown += 1
    return shown


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Run one heartbeat cycle.")
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument("--once", action="store_true", help="run a single cycle and exit")
    mode.add_argument("--watch", action="store_true", help="repeat on an interval (foreground)")
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help=(
            "use the built-in sample inbox; makes no Gmail request at all. "
            "Still a real cycle: it records state and may write a report, so "
            "point MANAGER_STATE_PATH somewhere temporary to keep sample data "
            "out of your real history."
        ),
    )
    parser.add_argument("--interval", type=int, default=None, help="minutes between cycles")
    args = parser.parse_args(argv)

    settings = Settings.from_env()
    configure_logging(settings.log_level)

    # A manual invocation enables the heartbeat for this run only; scheduled
    # use still requires MANAGER_HEARTBEAT_ENABLED=true.
    enabled = settings.heartbeat_is_enabled or args.once or args.watch or args.dry_run
    mailbox_label = "sample inbox (no Gmail)" if args.dry_run else settings.mailbox

    print(f"mailbox  : {mailbox_label}")
    print(f"profile  : {settings.profile_path}")
    print(f"schedule : {settings.schedule_path}")
    print(f"state    : {settings.state_path}")
    print(f"reports  : {settings.reports_dir}")
    print(f"enabled  : {enabled}")
    print("-" * 60)

    try:
        check_configuration(settings)
        runner = build_runner(settings, enabled=enabled, dry_run=args.dry_run)
    except (ConfigurationError, ProviderConfigurationError, TimezoneError) as exc:
        # Stop before touching the mailbox. An unattended run that silently
        # continued without the configured model would produce reports nobody
        # could tell apart from properly reasoned ones.
        print(f"configuration error: {exc}")
        return 2

    if args.watch:
        interval = (args.interval or settings.heartbeat_interval) * 60
        print(f"watching; one cycle every {interval // 60} minute(s). Ctrl-C to stop.")
        try:
            while True:
                print(runner.tick().describe())
                if enabled:
                    print(check_proactive_events(settings, args.dry_run))
                time.sleep(interval)
        except KeyboardInterrupt:
            print("\nstopped.")
            return 0

    result = runner.tick()
    print(result.describe())
    # Gated by the identical `enabled` flag as the email cycle above -
    # autonomy is opt-in, never a side effect of upgrading, and a
    # proactive-milestone check is exactly the kind of autonomous
    # behaviour that rule already covers.
    if enabled:
        print(check_proactive_events(settings, args.dry_run))
    return 0 if result.status is not TickStatus.FAILED else 1


if __name__ == "__main__":
    raise SystemExit(main())
