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
from pathlib import Path
from typing import Callable

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from src.config.coherence import (  # noqa: E402
    ConfigurationError,
    check_configuration,
)
from src.config.settings import Settings  # noqa: E402
from src.composition import build_manager, build_runtime  # noqa: E402
from src.providers.factory import ProviderConfigurationError  # noqa: E402
from src.services.memory.day import TimezoneError  # noqa: E402
from src.heartbeat.runner import HeartbeatRunner, TickStatus  # noqa: E402
from src.heartbeat.state import StateStore  # noqa: E402
from src.manager.manager import ManagerAgent  # noqa: E402
from src.services.email.mailbox_factory import build_mailbox  # noqa: E402
from src.services.email.sample_inbox import sample_inbox  # noqa: E402
from src.services.identity.soul import load_soul  # noqa: E402
from src.services.profile.loader import load_profile  # noqa: E402
from src.services.profile.profile import UserProfile  # noqa: E402
from src.services.schedule.loader import load_schedule  # noqa: E402
from src.services.schedule.schedule import UserSchedule  # noqa: E402
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
    runtime = build_runtime(settings)

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
        return build_manager(
            runtime,
            mailbox=mailbox,
            profile=profile,
            # Context about the person, freshly read this tick. Nothing here
            # reads it: it is handed to the Manager and never consulted to
            # decide whether this wake happens.
            schedule=schedule,
            fetch_limit=settings.heartbeat_limit,
        )

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
                time.sleep(interval)
        except KeyboardInterrupt:
            print("\nstopped.")
            return 0

    result = runner.tick()
    print(result.describe())
    return 0 if result.status is not TickStatus.FAILED else 1


if __name__ == "__main__":
    raise SystemExit(main())
