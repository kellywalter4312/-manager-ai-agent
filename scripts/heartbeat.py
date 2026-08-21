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

from src.agents.email.analyzers import JobOpportunityAnalyzer  # noqa: E402
from src.agents.brain_factory import (  # noqa: E402
    build_specialist_brain,
    domain_for,
)
from src.agents.email_agent import EmailAgent  # noqa: E402
from src.config.coherence import (  # noqa: E402
    ConfigurationError,
    check_configuration,
)
from src.config.settings import Settings  # noqa: E402
from src.heartbeat.runner import HeartbeatRunner, TickStatus  # noqa: E402
from src.heartbeat.state import StateStore  # noqa: E402
from src.manager.brain_factory import build_brain  # noqa: E402
from src.manager.manager import ManagerAgent  # noqa: E402
from src.manager.manager_skills import ManagerSkills  # noqa: E402
from src.manager.router_factory import build_router  # noqa: E402
from src.providers.factory import (  # noqa: E402
    ProviderConfigurationError,
    build_provider,
)
from src.registry.agent_registry import AgentRegistry  # noqa: E402
from src.services.memory.factory import (  # noqa: E402
    EMAIL_AGENT,
    MANAGER,
    build_memory,
)
from src.services.email.mailbox_factory import build_mailbox  # noqa: E402
from src.services.email.sample_inbox import sample_inbox  # noqa: E402
from src.services.identity.identity import load_identity  # noqa: E402
from src.services.identity.soul import load_soul  # noqa: E402
from src.services.profile.loader import load_profile  # noqa: E402
from src.services.profile.profile import UserProfile  # noqa: E402
from src.utils.logging import configure_logging  # noqa: E402


def build_manager_factory(settings: Settings, dry_run: bool) -> Callable[[UserProfile], ManagerAgent]:
    """A factory that assembles a fully wired Manager for a given profile.

    A factory, not an instance: USER.md is re-read on every tick and the
    profile is baked into the analyzer, so a fresh profile means a freshly
    assembled agent. The mailbox is built once and shared - it holds the
    lazily created API client.
    """
    identity = load_identity(settings.identity_path)
    provider = build_provider(settings)
    router = build_router(settings, provider)
    # Built once and shared across ticks: a brain is stateless by design, so
    # there is nothing per-tick about it. When memory arrives it will be the
    # component with a lifecycle, not this one.
    brain = build_brain(settings, provider)
    # Built once and shared for the same reason the Manager's is: a brain holds
    # no per-tick state. Wired here as well as in main.py so a configured
    # specialist brain is not silently discarded on the autonomous path - the
    # exact "built and then thrown away" failure the coherence check exists to
    # prevent one layer up.
    specialist_brain = build_specialist_brain(
        settings, provider, domain_for("email_agent")
    )
    mailbox = sample_inbox() if dry_run else build_mailbox(settings)

    def build(profile: UserProfile) -> ManagerAgent:
        # Registry first, so the Manager's own skills can report the roster
        # without holding a reference back to the Manager itself.
        registry = AgentRegistry()
        skills = ManagerSkills(identity, roster=registry.all_agents)

        # Built per tick, not once, and that is the point. A persistent store
        # re-reads its file at construction, so this tick sees what the last
        # one wrote even though the Manager object itself does not survive.
        # The Manager needs no lifecycle; its memory does.
        manager = ManagerAgent(
            registry=registry,
            router=router,
            brain=brain,
            own_skills=skills,
            memory=build_memory(settings, MANAGER),
        )
        manager.register_agent(
            EmailAgent(
                mailbox,
                analyzers=[JobOpportunityAnalyzer(profile=profile)],
                brain=specialist_brain,
                memory=build_memory(settings, EMAIL_AGENT),
                fetch_limit=settings.heartbeat_limit,
            )
        )
        return manager

    return build


def build_runner(settings: Settings, enabled: bool, dry_run: bool) -> HeartbeatRunner:
    return HeartbeatRunner(
        build_manager=build_manager_factory(settings, dry_run),
        profile_loader=lambda: load_profile(settings.profile_path),
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
    print(f"state    : {settings.state_path}")
    print(f"reports  : {settings.reports_dir}")
    print(f"enabled  : {enabled}")
    print("-" * 60)

    try:
        check_configuration(settings)
        runner = build_runner(settings, enabled=enabled, dry_run=args.dry_run)
    except (ConfigurationError, ProviderConfigurationError) as exc:
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
