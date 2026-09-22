"""Render the interface against the real system.

    python scripts/export_ui.py            -> ui/agent-control-center.html

Takes one snapshot of the running system and writes it into the interface
shell, producing a single self-contained page that can be opened on a laptop or
a phone.

**Why a file and not a server.** A server is a real decision - a dependency, a
port, a lifetime, a security surface - and this phase is the interface
*foundation*, not the delivery mechanism. A static page proves the whole path
(system -> snapshot -> interface) with nothing added to requirements.txt, and
leaves the serving question open rather than answering it by accident. When a
live interface is wanted, the snapshot is already the seam it reads from.

The page shows what the system was doing when this ran. It says so at the top,
because a stale reading presented as live would be the exact dishonesty the
snapshot module exists to prevent.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from src.composition import (  # noqa: E402
    register_defined_specialists,
    build_channel,
    build_conversation,
    build_manager,
    build_runtime,
)
from src.config.coherence import ConfigurationError, check_configuration  # noqa: E402
from src.config.settings import Settings  # noqa: E402
from src.control_center.observer import ControlCenter  # noqa: E402
from src.control_center.snapshot import snapshot  # noqa: E402
from src.providers.factory import ProviderConfigurationError  # noqa: E402
from src.services.memory.day import TimezoneError  # noqa: E402
from src.services.credentials.account_store_file import (  # noqa: E402
    DEFAULT_ACCOUNTS_PATH,
    load_credential_store,
)
from src.services.email.mailbox_factory import build_mailbox  # noqa: E402
from src.services.profile.loader import load_profile  # noqa: E402
from src.services.profile.profile import ProfileError  # noqa: E402
from src.services.evolution.governed_config import (  # noqa: E402
    DEFAULT_CONFIG_PATH,
    load_governed_config,
)
from src.services.schedule.loader import load_schedule  # noqa: E402
from src.utils.logging import configure_logging  # noqa: E402

from scripts.control_center import (  # noqa: E402
    SpecialistCapabilities,
    equip_specialist,
    filtered_manager_capabilities,
)

SHELL = Path(__file__).resolve().parent.parent / "ui" / "index.html"
PLACEHOLDER = "__SNAPSHOT__"


def render(data: dict, shell: Path = SHELL) -> str:
    """Put one snapshot into the shell.

    The data is embedded as JSON inside a script tag rather than interpolated
    into the markup, so nothing an agent said can become part of the page. A
    conversation carries whatever a person typed, and a transcript that could
    rewrite the interface would be a hole with a screen in front of it.
    """
    template = shell.read_text(encoding="utf-8")
    if PLACEHOLDER not in template:
        raise ValueError(f"{shell} has no {PLACEHOLDER} to fill")
    # </script> inside the payload would end the tag early; escaping the slash
    # is the standard, and JSON-safe, way to prevent it.
    payload = json.dumps(data).replace("</", "<\\/")
    return template.replace(PLACEHOLDER, payload)


def take_snapshot(settings: Settings) -> dict:
    """Assemble the real system and read one honest snapshot of it.

    Called once by `main()` below for a static export, and once per request
    by `serve_ui.py` for a live one. Same assembly either way, on purpose:
    a live reading and a static one must never be able to quietly diverge
    into two different ideas of what the system looks like.

    Universal provider/connection management repair: loads the same
    persisted ``CredentialStore`` ``scripts/control_center.py::build``
    now does, and passes each category's active account into
    ``build_runtime`` the identical way - this snapshot must not report
    a system built from a different credential configuration than the
    real terminal Control Center would.

    Capability-registry repair: filters the manager's own capability
    registry through the identical persisted ``GovernedConfig``
    ``scripts/control_center.py::build`` already consults, so a
    capability disabled through Control Center is reported as disabled
    here too - a static export built from a Manager that still carried
    every disabled capability as live would be exactly the stale,
    dishonest reading this module's own docstring says it must not
    produce.
    """
    accounts = load_credential_store(DEFAULT_ACCOUNTS_PATH)
    runtime = build_runtime(
        settings,
        accounts=accounts,
        video_generation_account=accounts.active(settings.video_generation),
        speech_account=accounts.active(settings.speech),
        communication_account=accounts.active(settings.communication_provider),
    )
    try:
        profile = load_profile(settings.profile_path)
    except ProfileError:
        profile = None

    capabilities = filtered_manager_capabilities(load_governed_config(DEFAULT_CONFIG_PATH))
    manager = build_manager(
        runtime,
        mailbox=build_mailbox(settings),
        profile=profile,
        schedule=load_schedule(settings.schedule_path),
        capabilities=capabilities,
    )
    # The owner's own specialists, exactly as every other entry point
    # registers them - a snapshot without them would describe a different
    # system than the one the Control Center runs.
    register_defined_specialists(runtime, manager, load_governed_config(DEFAULT_CONFIG_PATH).values, accounts=accounts)
    centre = ControlCenter(runtime, manager)
    conversation = build_conversation(manager)
    channel = build_channel(runtime, manager)
    # Equipped exactly as control_center.build equips them - otherwise a
    # specialist's granted capabilities are missing from the export while the
    # served page, reading the same grants, shows them.
    manager.specialist_capabilities = SpecialistCapabilities(
        runtime=runtime, manager=manager, channel=channel, conversation=conversation,
    )
    for agent in manager.registry.all_agents():
        if agent is not manager.own_skills:
            equip_specialist(agent, runtime=runtime, manager=manager, channel=channel)

    return snapshot(centre, conversation=conversation, channel=channel)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Render the interface.")
    parser.add_argument(
        "--out",
        default=str(SHELL.parent / "agent-control-center.html"),
        help="where to write the page",
    )
    parser.add_argument("--json", default=None, help="also write the raw snapshot here")
    args = parser.parse_args(argv)

    settings = Settings.from_env()
    configure_logging("WARNING")

    try:
        check_configuration(settings)
        data = take_snapshot(settings)
    except (ConfigurationError, ProviderConfigurationError, TimezoneError) as exc:
        # Final comprehensive audit (2026-09-08): the identical guard
        # every other real entry point already runs before building
        # anything - found missing here. Exporting a static page from an
        # incoherent configuration (or one whose provider cannot actually
        # be constructed, e.g. a missing API key - take_snapshot() is
        # where that surfaces, exactly as it does in every other entry
        # point's own build call) would produce a page describing a
        # system that could never actually run.
        print(f"configuration error: {exc}")
        return 2

    out = Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(render(data), encoding="utf-8")
    print(f"wrote {out}")

    if args.json:
        Path(args.json).write_text(json.dumps(data, indent=2), encoding="utf-8")
        print(f"wrote {args.json}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
