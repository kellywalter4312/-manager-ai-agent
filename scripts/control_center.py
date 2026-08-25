"""The Agent Control Center - the front door to Alfred.

    python scripts/control_center.py              interactive
    python scripts/control_center.py status       one overview, then exit
    python scripts/control_center.py status Arthur
    python scripts/control_center.py history --category memory
    python scripts/control_center.py talk "scan my inbox for job opportunities"

Three ways in, and no more:

    TALK TO MANAGER   say what you want; Alfred decides how it gets done
    STATUS            what is happening now
    HISTORY           what happened

**This file owns nothing.** It builds the system through the same composition
root main.py and the heartbeat use, then reads it through the Control Center.
It does not route, match a capability, assess a brain, admit a memory, register
a specialist or schedule a wake - every one of those belongs to a component
that already exists, and duplicating any of them here would make the front door
a second implementation of the house.

**You never pick a specialist.** You talk to Alfred. He decides whether he can
take the work on himself, whether it needs a tool he does not have, or whether
a specialist is better suited - and the trail printed beside each answer shows
which of those happened. That is the difference between an agent system and a
chat box, and it is why the trail is shown by default rather than hidden behind
a flag.

Read-only throughout, exactly as every other entry point: the Gmail token
carries gmail.readonly, no Outbox implementation exists, and nothing here can
send, reply, apply, bid or contact anyone.
"""

from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from src.composition import build_manager, build_runtime  # noqa: E402
from src.config.coherence import ConfigurationError, check_configuration  # noqa: E402
from src.config.settings import Settings  # noqa: E402
from src.control_center.observer import ControlCenter  # noqa: E402
from src.control_center.render import (  # noqa: E402
    render_agent,
    render_history,
    render_overview,
    render_system,
    render_turn,
    render_voice,
    width,
)
from src.control_center.trail import observe  # noqa: E402
from src.control_center.views import VoiceView  # noqa: E402
from src.core.contracts import Task  # noqa: E402
from src.providers.factory import ProviderConfigurationError  # noqa: E402
from src.services.email.mailbox_factory import build_mailbox  # noqa: E402
from src.services.memory.day import TimezoneError  # noqa: E402
from src.services.profile.loader import load_profile  # noqa: E402
from src.services.profile.profile import ProfileError  # noqa: E402
from src.services.schedule.loader import load_schedule  # noqa: E402
from src.utils.logging import configure_logging  # noqa: E402

HELP = """
  talk <what you want>     ask Alfred to do something
  status                   the overview
  status <agent>           one agent: brain, memory, capabilities, tools
  status system            heartbeat, permissions, tools, health
  history                  what happened
  history <category>       filter: memory, report
  voice                    whether Alfred can hear and speak
  review                   what Alfred has noticed about his own system
  help                     this
  quit                     leave
""".strip("\n")


def build(settings: Settings):
    """Assemble the system exactly as the other entry points do.

    Same composition root, same per-run documents. The Control Center observes
    what everything else already builds rather than arranging its own version,
    which is what keeps the screen describing the real program.
    """
    runtime = build_runtime(settings)

    try:
        profile = load_profile(settings.profile_path)
    except ProfileError:
        # Degrades rather than stopping, as main.py does: a Control Center that
        # refused to open because USER.md was missing would be useless exactly
        # when someone needs to look at why.
        profile = None

    manager = build_manager(
        runtime,
        mailbox=build_mailbox(settings),
        profile=profile,
        schedule=load_schedule(settings.schedule_path),
    )
    return runtime, manager


def run_command(centre: ControlCenter, command: str, columns: int) -> str:
    """One command, one screen. Overview first, detail on demand."""
    command = command.strip()
    if not command:
        return ""

    verb, _, rest = command.partition(" ")
    verb, rest = verb.lower(), rest.strip()

    if verb in ("help", "?"):
        return HELP

    if verb == "status":
        overview = centre.overview()
        if not rest:
            return render_overview(overview, columns)
        if rest.lower() == "system":
            return render_system(overview, columns)
        agent = overview.agent(rest)
        if agent is None:
            known = ", ".join(a.name for a in overview.agents)
            return f"no agent called '{rest}'. Registered: {known}"
        return render_agent(agent, columns)

    if verb == "history":
        category = rest.lower() or None
        view = centre.history(category=category)
        if category and not view.events and category not in view.categories:
            return (
                f"no events in category '{category}'. "
                f"Available: {', '.join(view.categories) or 'none yet'}"
            )
        return render_history(view, columns)

    if verb == "voice":
        # Reported, never asserted. What is shown is the adapter's own
        # Availability - three states, not a boolean - so an engine that has
        # not been proven reads as unproven rather than as missing.
        #
        # NoSpeech is still constructed here rather than obtained from the
        # composition root, because nothing composes a speech adapter yet.
        # That is honest today - it is genuinely what the system has - and it
        # is the one line that changes when speech joins the Runtime.
        from src.channels.speech import NoSpeech

        speech = NoSpeech()
        return render_voice(
            VoiceView(engine=speech.name, availability=speech.availability), columns
        )

    if verb == "review":
        # Observation only. Nothing here changes anything, and there is no
        # command that would - applying a proposal is a person's decision.
        from src.improvement.governance import ImprovementBoard

        board = ImprovementBoard()
        observations = board.review(centre.manager)
        proposals = board.propose()

        lines = ["SELF-REVIEW", f"  {board.summarise()}", ""]
        for observation in observations:
            lines.append(f"  [{observation.severity.value}] {observation.subject}")
            lines += [f"    {line.strip()}" for line in [observation.finding]]
        if proposals:
            lines.append("")
            lines.append("  PROPOSALS (all require your approval; none is applied)")
            for proposal in proposals:
                lines.append(f"    {proposal.change.value}: {proposal.rationale}")
        return "\n".join(lines)

    if verb == "talk":
        if not rest:
            return "say what you would like Alfred to do, for example: talk scan my inbox"
        return render_turn(observe(centre.manager, Task(description=rest)), columns)

    # Anything else is treated as something to say to Alfred. Talking is the
    # primary mode, so it should not need a keyword.
    return render_turn(observe(centre.manager, Task(description=command)), columns)


def interactive(centre: ControlCenter, columns: int) -> int:
    print(render_overview(centre.overview(), columns))
    print()
    print("type 'help' for commands, or just say what you want.")

    while True:
        try:
            command = input("\n> ").strip()
        except (EOFError, KeyboardInterrupt):
            print()
            return 0
        if command.lower() in ("quit", "exit"):
            return 0
        print()
        print(run_command(centre, command, columns))


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="The Agent Control Center.")
    parser.add_argument("command", nargs="*", help="status | history | talk <request>")
    parser.add_argument("--category", default=None, help="filter history by category")
    parser.add_argument("--width", type=int, default=None, help="render for this many columns")
    args = parser.parse_args(argv)

    settings = Settings.from_env()

    # A screen, not a log. INFO-level records narrate every construction and
    # would bury the answer a person came to read, so the Control Center is
    # quiet by default - and stays exactly as loud as asked when someone sets
    # MANAGER_LOG_LEVEL deliberately. A presentation decision, which is the
    # only kind this file is allowed to make.
    configure_logging(
        settings.log_level if "MANAGER_LOG_LEVEL" in os.environ else "WARNING"
    )

    try:
        check_configuration(settings)
        runtime, manager = build(settings)
    except (ConfigurationError, ProviderConfigurationError, TimezoneError) as exc:
        # Stop before showing anything. A Control Center that opened on a
        # misconfigured system would describe a program that is not running.
        print(f"configuration error: {exc}")
        return 2

    centre = ControlCenter(runtime, manager)
    columns = args.width or width()

    if not args.command:
        return interactive(centre, columns)

    command = " ".join(args.command)
    if args.category and command.strip().lower().startswith("history"):
        command = f"history {args.category}"
    print(run_command(centre, command, columns))
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
