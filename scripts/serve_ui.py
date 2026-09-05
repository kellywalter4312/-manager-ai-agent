"""Serve the interface against the live running system - and now talk to it.

    python scripts/serve_ui.py [--host 127.0.0.1] [--port 8642]

**Phase 6 changes what "live" means here.** The read-only milestone before
this one rebuilt the whole system fresh on every request - correct for
observation, and exactly wrong for interaction: a message sent against a
freshly-rebuilt Manager would be answered by one with no memory of the
previous turn, in a room that had just been recreated empty. So this file
now assembles **one** system - one `runtime`, one `ManagerAgent`, one
`Channel`, one `Conversation` - when the process starts, and holds it for
as long as the process runs. Every GET reads that one system; the one POST
route acts on it. This is not a second orchestrator, a second Brain or a
second Conversation architecture: it is the same one
`scripts/control_center.py`'s `interactive()` loop already holds for a
terminal session, held here instead for an HTTP session. Nothing about
*how* a request is answered changed - `build_live_system` calls the exact
function that already builds that session (`scripts.control_center.build`),
and `POST /message` calls the exact function a terminal `talk` command
already calls (`scripts.exchange.exchange`).

**Why this doesn't weaken the no-server guard.**
`tests/test_architecture_audit.py::test_no_server_or_voice_module_is_imported`
forbids `src/` from importing `http.server`/`socketserver` (among others) -
a guard that keeps the domain layer free of serving concerns, not one that
forbids this project from ever serving anything. That test only scans
`src/`. `scripts/` is already where every entry point lives (`main.py`,
`scripts/heartbeat.py`, `scripts/control_center.py`, `scripts/export_ui.py`)
without being part of the architecture those files assemble. This file
joins them; it does not cross the boundary the guard protects.

Stdlib only - no dependency was added to reach this.

Seven routes:

    GET  /              the rendered page, current as of the live system now
    GET  /snapshot.json the same data, as JSON, for the page's own refresh
    POST /message       {"text": "...", "to": "<agent name>"?} -> one
                         exchange, honestly reported. "to" is Stage 17's
                         explicit agent-selector: an override resolved
                         through the same registry lookup the Manager
                         already uses (scripts/exchange.py's own
                         ``addressed`` parameter), never a new routing
                         mechanism. Omitted or unrecognised, addressing
                         falls back to the words themselves, exactly as
                         before this route existed. Stage 17.3: also the
                         real production speech/presence path - see
                         route_message()'s own docstring below.
    POST /avatar        {"agent": "...", "avatar_id": "..."} -> Stage 17's
                         governed avatar-preference write, through the
                         identical set_avatar_preference() a terminal
                         session's own ``avatar`` command calls - never a
                         second write path.
    POST /profile       {"agent": "...", "fields": {"posture": "...", ...}}
                         -> Stage 17.4's governed DigitalHumanProfile style
                         write, through the identical
                         set_digital_human_style() a terminal session's
                         own ``profile`` command calls. Never the avatar
                         itself - that stays POST /avatar above.
    POST /environment   {"agent": "...", "environment_id": "..."} ->
                         Whole-System Reality Audit's governed
                         environment-preference write, through the
                         identical set_environment_preference() a
                         terminal session's own ``environment`` command
                         calls - never a second write path.
    GET  /vendor/...    a fixed, explicit allowlist of static presentation
                         assets (today: the vendored Three.js build and the
                         one font ui/index.html actually declares) - never
                         an arbitrary-path file server

Nothing else - no PUT, no DELETE, no session, no auth beyond whatever
`build_live_system` already wires (today: no tools, same as `main.py`).
Binds to loopback by default; reaching it from another machine is an
explicit `--host` choice.

**Concurrency.** GET reads are unlocked - each call to `snapshot()` builds a
fresh dict from state that is only ever appended to, never rewritten, so
there is nothing for a concurrent read to see half-finished. `POST
/message` is different: `exchange()` says something in the room, asks the
Manager, and may retain a memory - four steps that must happen as one, or
two requests arriving together could interleave into a transcript that
never happened. `LiveSystem.lock` serialises exactly that one call, and
nothing else.
"""

from __future__ import annotations

import argparse
import json
import sys
import threading
from dataclasses import dataclass
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from scripts.control_center import build as assemble_system  # noqa: E402
from scripts.exchange import exchange, memory_for  # noqa: E402
from scripts.export_ui import render  # noqa: E402
from src.config.settings import Settings  # noqa: E402
from src.control_center.observer import ControlCenter  # noqa: E402
from src.control_center.snapshot import snapshot  # noqa: E402
from src.services.identity.presence import (  # noqa: E402
    PresenceState,
    set_avatar_preference,
    set_digital_human_style,
    set_environment_preference,
)
from src.utils.logging import configure_logging, get_logger  # noqa: E402

logger = get_logger(__name__)

DEFAULT_HOST = "127.0.0.1"
DEFAULT_PORT = 8642

VENDOR_DIR = Path(__file__).resolve().parent.parent / "ui" / "vendor"
# A fixed, explicit allowlist of static presentation assets - not an
# arbitrary-path file server. This route exists to hand the browser one
# vendored rendering library, nothing else; a path that isn't in this map
# is 404, the same as any other unknown route.
VENDOR_ASSETS = {
    "/vendor/three.module.min.js": ("three.module.min.js", "text/javascript; charset=utf-8"),
    # three.module.min.js imports this sibling module by relative path at
    # the browser's own request time - it is not optional.
    "/vendor/three.core.min.js": ("three.core.min.js", "text/javascript; charset=utf-8"),
    # The one font the interface actually declares. A stylesheet that names
    # a family nobody ships is a stylesheet that silently renders in
    # whatever the system happened to have - which is what ui/index.html
    # did for its whole life, declaring "IBM Plex Mono"/"Space Mono" and
    # shipping neither. Vendored at ui/vendor/CascadiaMono.ttf and loaded
    # by that file's own @font-face through a relative URL, so the static
    # export (opened from disk, beside ui/vendor/) and the served page
    # both resolve it and neither ever touches the network.
    "/vendor/CascadiaMono.ttf": ("CascadiaMono.ttf", "font/ttf"),
    # The two faces the typography system actually sets text in - a
    # display face for identity and headings, a text face for prose and
    # controls - alongside the mono above, which is now scoped to
    # metadata and technical readouts rather than being the whole
    # interface. Both are SIL OFL variable fonts, vendored with their
    # licences beside them (ui/vendor/INTER_LICENSE,
    # ui/vendor/SPACE_GROTESK_LICENSE); woff2 because a variable woff2
    # is one file for the whole weight range.
    "/vendor/SpaceGrotesk.woff2": ("SpaceGrotesk.woff2", "font/woff2"),
    "/vendor/Inter.woff2": ("Inter.woff2", "font/woff2"),
}


@dataclass
class LiveSystem:
    """One assembled system, held for the life of the server process.

    Exactly what `scripts/control_center.py`'s `interactive()` loop already
    holds for a terminal session - the same runtime, the same Manager, the
    same channel, the same conversation - just held here so a browser can
    ask the same questions and share the same room a terminal would. There
    is exactly one `manager`, built once by the one real composition root;
    nothing here is a second Brain, a second Memory or a second
    Conversation, because nothing here builds one - nothing here builds
    anything at all after construction. It only holds what
    `assemble_system` handed back.
    """

    runtime: Any
    manager: Any
    channel: Any
    conversation: Any
    centre: ControlCenter
    lock: threading.Lock


def build_live_system(settings: Settings) -> LiveSystem:
    """Assemble the one system this process will serve, once.

    Calls the exact function a terminal session calls to open
    (`scripts.control_center.build`) - not a second assembly path that
    could quietly drift from it.
    """
    runtime, manager, channel, conversation = assemble_system(settings)
    return LiveSystem(
        runtime=runtime,
        manager=manager,
        channel=channel,
        conversation=conversation,
        centre=ControlCenter(runtime, manager),
        lock=threading.Lock(),
    )


def _current_snapshot(system: LiveSystem) -> dict:
    """The one snapshot contract, read from the one held system."""
    return snapshot(system.centre, conversation=system.conversation, channel=system.channel)


def route(path: str, system: LiveSystem) -> tuple[int, bytes, str]:
    """Decide what to answer for one GET path. No socket involved - callable
    directly from a test.

    Reads the held system; builds nothing. `snapshot()` returns a fresh
    dict/tuple structure on every call even though the system underneath is
    the same one every time, so nothing a caller does to one response can
    reach back and change another.
    """
    if path == "/snapshot.json":
        data = _current_snapshot(system)
        return 200, json.dumps(data).encode("utf-8"), "application/json; charset=utf-8"
    if path == "/":
        data = _current_snapshot(system)
        return 200, render(data).encode("utf-8"), "text/html; charset=utf-8"
    if path in VENDOR_ASSETS:
        filename, content_type = VENDOR_ASSETS[path]
        return 200, (VENDOR_DIR / filename).read_bytes(), content_type
    return 404, b"not found", "text/plain; charset=utf-8"


def route_message(
    text: object, system: LiveSystem, *, to: object = None
) -> tuple[int, bytes, str]:
    """Decide what to answer for `POST /message`. No socket involved.

    Malformed or empty text never reaches `Conversation.say()` or
    `ManagerAgent.handle_task()` - both live only inside `exchange()`, and
    this function returns before calling it.

    ``to``, when a non-empty string, is passed through as `exchange()`'s own
    ``addressed`` override - Stage 17's explicit agent-selector. Anything
    else (absent, empty, not a string) is treated as "no explicit choice",
    falling back to `exchange()`'s own text-inferred addressing - the exact
    behaviour this route already had before ``to`` existed.

    **Stage 17.3: this is the real production path Agent Presence is wired
    to.** ``exchange(..., speak=True)`` is called - the identical function
    a terminal ``talk`` command uses, now also asking the channel to
    ``deliver()`` the answer, which is the one real place a real (if
    unconfigured, honestly instant) ``speak()`` call happens and the one
    real place SPEAKING is asserted (see ``scripts/exchange.py`` and
    ``VoiceChannel.deliver()``). No new speech path, no fake timer: this
    route was already the production entry point for a typed message,
    and now genuinely reaches all the way to real playback and back.

    The one mutating call is made under `system.lock`: `exchange()` speaks
    in the room, asks the Manager, and may retain a memory, and two of
    those interleaving on two threads would be two people talking over each
    other in the same room. A failure inside it is reported as a failure -
    never turned into an invented success. Deliberately *not* held across
    the whole call is a live poll of ``GET /snapshot.json``: `PresenceRegistry`
    has its own, separate lock (see presence.py), so a concurrent read
    genuinely can observe SPEAKING while this one is still inside a real,
    blocking engine - not merely possible in theory, proven in
    tests/test_stage17_3_live_speaking_presence.py.

    **Stage 17.16: the same real LISTENING signal
    ``VoiceActivationSession.hear_activation()`` already asserts for the
    ambient-voice flow, now asserted here too - for exactly the one case
    where who is about to receive this request is genuinely known before
    ``exchange()`` does any routing: an explicit ``to``. An unaddressed
    message is resolved by ``exchange()``'s own routing, which this
    function has no way to know the outcome of in advance - guessing
    would be exactly the "asserted, not derived" mistake this module
    refuses, so no LISTENING signal is asserted for that case at all,
    honestly. Cleared in ``finally`` so a raised exception can never
    leave a stale LISTENING behind - the identical exception-safety
    ``speaking()`` itself already has. Never a second presence
    mechanism: the same shared ``PresenceRegistry``
    ``VoiceChannel.deliver()``'s own SPEAKING assertion already writes
    to, through its own, already-existing ``set``/``clear`` pair.
    """
    if not isinstance(text, str) or not text.strip():
        return (
            400,
            json.dumps({"error": "message text must not be empty"}).encode("utf-8"),
            "application/json; charset=utf-8",
        )

    addressed = to.strip() if isinstance(to, str) and to.strip() else None
    # Only a real, registered agent is marked LISTENING - the identical
    # "is this an agent?" guard VoiceChannel.deliver() itself already
    # applies before attributing SPEAKING.
    listening_for = addressed if addressed and system.manager.registry.get(addressed) is not None else None

    presence = system.runtime.presence_registry
    if listening_for:
        presence.set(listening_for, PresenceState.LISTENING)
    try:
        with system.lock:
            turn = exchange(
                system.channel, system.conversation, text.strip(),
                addressed=addressed, speak=True,
            )
    except Exception:  # noqa: BLE001 - an HTTP boundary must not crash the process
        logger.exception("live message handling failed")
        return (
            500,
            json.dumps({"error": "the system failed to handle that message"}).encode("utf-8"),
            "application/json; charset=utf-8",
        )
    finally:
        if listening_for:
            presence.clear(listening_for)

    body = json.dumps(
        {
            "handled_by": turn.handled_by,
            "delegated": turn.delegated,
            "outcome": turn.outcome,
            "succeeded": turn.succeeded,
            "brain_mode": turn.brain_mode,
            "specialist_brain_mode": turn.specialist_brain_mode,
            "memory_written": turn.memory_written,
            "invited": turn.invited,
            # Stage 12 closure: the presentation-shaped reaction a genuine
            # milestone produced, if any - the real event/consumption
            # boundary a future avatar/environment renderer would read
            # from. None for every ordinary turn.
            "celebration": turn.celebration,
        }
    ).encode("utf-8")
    return 200, body, "application/json; charset=utf-8"


def route_avatar(agent: object, avatar_id: object, system: LiveSystem) -> tuple[int, bytes, str]:
    """Decide what to answer for `POST /avatar`. No socket involved.

    Resolves ``agent`` to its own real Memory through `memory_for()`
    (scripts/exchange.py) - the same rule
    `src/control_center/observer.py` already applies, shared rather than
    reimplemented here - and never a new agent: an unrecognised name
    writes nothing.

    The write itself is `set_avatar_preference()`, the identical governed
    path a terminal session's own `avatar` command uses - this route is
    another caller of it, not a second implementation.
    """
    if not isinstance(agent, str) or not agent.strip():
        return (
            400,
            json.dumps({"error": "agent must not be empty"}).encode("utf-8"),
            "application/json; charset=utf-8",
        )
    if not isinstance(avatar_id, str) or not avatar_id.strip():
        return (
            400,
            json.dumps({"error": "avatar_id must not be empty"}).encode("utf-8"),
            "application/json; charset=utf-8",
        )

    memory = memory_for(system.manager, agent)

    if memory is None:
        return (
            404,
            json.dumps({"error": f"no such agent: {agent!r}"}).encode("utf-8"),
            "application/json; charset=utf-8",
        )

    with system.lock:
        ok = set_avatar_preference(
            agent,
            avatar_id,
            provider=system.runtime.avatar_provider,
            recall=memory.recall,
            remember=memory.remember,
            forget=memory.forget,
        )

    if not ok:
        return (
            400,
            json.dumps({"error": f"no such avatar: {avatar_id!r}"}).encode("utf-8"),
            "application/json; charset=utf-8",
        )
    return (
        200,
        json.dumps({"agent": agent, "avatar_id": avatar_id.strip().lower()}).encode("utf-8"),
        "application/json; charset=utf-8",
    )


def route_profile(agent: object, fields: object, system: LiveSystem) -> tuple[int, bytes, str]:
    """Decide what to answer for `POST /profile`. No socket involved.

    Stage 17.4: the write path for the *other* dimensions of a
    ``DigitalHumanProfile`` - posture, clothing, idle/listening/
    speaking behaviour, expression and gesture style - never the
    avatar itself, which stays exactly `POST /avatar` above.

    Resolves ``agent`` through `memory_for()`, the identical rule
    every other write route in this file already applies - an
    unrecognised name writes nothing. The write itself is
    `set_digital_human_style()`, the identical governed path a
    terminal session's own `profile` command uses - this route is
    another caller of it, not a second implementation.
    """
    if not isinstance(agent, str) or not agent.strip():
        return (
            400,
            json.dumps({"error": "agent must not be empty"}).encode("utf-8"),
            "application/json; charset=utf-8",
        )
    if not isinstance(fields, dict) or not fields:
        return (
            400,
            json.dumps({"error": "fields must be a non-empty object"}).encode("utf-8"),
            "application/json; charset=utf-8",
        )
    if not all(isinstance(k, str) and isinstance(v, str) for k, v in fields.items()):
        return (
            400,
            json.dumps({"error": "every field name and value must be a string"}).encode("utf-8"),
            "application/json; charset=utf-8",
        )

    memory = memory_for(system.manager, agent)

    if memory is None:
        return (
            404,
            json.dumps({"error": f"no such agent: {agent!r}"}).encode("utf-8"),
            "application/json; charset=utf-8",
        )

    with system.lock:
        ok = set_digital_human_style(
            agent, fields, recall=memory.recall, remember=memory.remember, forget=memory.forget
        )

    if not ok:
        return (
            400,
            json.dumps(
                {"error": f"invalid field(s) or empty value(s) in {fields!r}"}
            ).encode("utf-8"),
            "application/json; charset=utf-8",
        )
    return (
        200,
        json.dumps({"agent": agent, "fields": fields}).encode("utf-8"),
        "application/json; charset=utf-8",
    )


def route_environment(agent: object, environment_id: object, system: LiveSystem) -> tuple[int, bytes, str]:
    """Decide what to answer for `POST /environment`. No socket involved.

    Whole-System Reality Audit: the identical write path a terminal
    session's own `environment` command now calls
    (`set_environment_preference()`) - found missing entirely until
    that audit, the same gap avatar itself had before Stage 17's own
    preference work. This route is another caller of it, never a
    second implementation, mirroring `route_avatar` exactly.
    """
    if not isinstance(agent, str) or not agent.strip():
        return (
            400,
            json.dumps({"error": "agent must not be empty"}).encode("utf-8"),
            "application/json; charset=utf-8",
        )
    if not isinstance(environment_id, str) or not environment_id.strip():
        return (
            400,
            json.dumps({"error": "environment_id must not be empty"}).encode("utf-8"),
            "application/json; charset=utf-8",
        )

    memory = memory_for(system.manager, agent)

    if memory is None:
        return (
            404,
            json.dumps({"error": f"no such agent: {agent!r}"}).encode("utf-8"),
            "application/json; charset=utf-8",
        )

    with system.lock:
        ok = set_environment_preference(
            agent,
            environment_id,
            provider=system.runtime.environment_provider,
            recall=memory.recall,
            remember=memory.remember,
            forget=memory.forget,
        )

    if not ok:
        return (
            400,
            json.dumps({"error": f"no such environment: {environment_id!r}"}).encode("utf-8"),
            "application/json; charset=utf-8",
        )
    return (
        200,
        json.dumps({"agent": agent, "environment_id": environment_id.strip().lower()}).encode("utf-8"),
        "application/json; charset=utf-8",
    )


class LiveServer(ThreadingHTTPServer):
    """A `ThreadingHTTPServer` carrying exactly one thing: the one system
    every request reads or acts on."""

    def __init__(self, address: tuple[str, int], system: LiveSystem) -> None:
        super().__init__(address, SnapshotHandler)
        self.system = system


class SnapshotHandler(BaseHTTPRequestHandler):
    server: LiveServer

    def do_GET(self) -> None:  # noqa: N802 - stdlib's naming, not ours to change
        status, body, content_type = route(self.path, self.server.system)
        self._respond(status, body, content_type)

    def do_POST(self) -> None:  # noqa: N802 - stdlib's naming, not ours to change
        if self.path not in ("/message", "/avatar", "/profile", "/environment"):
            self._respond(404, b"not found", "text/plain; charset=utf-8")
            return

        length = int(self.headers.get("Content-Length") or 0)
        raw = self.rfile.read(length) if length > 0 else b""
        try:
            payload = json.loads(raw) if raw.strip() else {}
        except json.JSONDecodeError:
            self._respond(
                400,
                json.dumps({"error": "malformed JSON"}).encode("utf-8"),
                "application/json; charset=utf-8",
            )
            return

        if self.path == "/message":
            text = payload.get("text") if isinstance(payload, dict) else None
            to = payload.get("to") if isinstance(payload, dict) else None
            status, body, content_type = route_message(text, self.server.system, to=to)
        elif self.path == "/avatar":
            agent = payload.get("agent") if isinstance(payload, dict) else None
            avatar_id = payload.get("avatar_id") if isinstance(payload, dict) else None
            status, body, content_type = route_avatar(agent, avatar_id, self.server.system)
        elif self.path == "/environment":
            agent = payload.get("agent") if isinstance(payload, dict) else None
            environment_id = payload.get("environment_id") if isinstance(payload, dict) else None
            status, body, content_type = route_environment(agent, environment_id, self.server.system)
        else:
            agent = payload.get("agent") if isinstance(payload, dict) else None
            fields = payload.get("fields") if isinstance(payload, dict) else None
            status, body, content_type = route_profile(agent, fields, self.server.system)
        self._respond(status, body, content_type)

    def _respond(self, status: int, body: bytes, content_type: str) -> None:
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, format: str, *args) -> None:  # noqa: A002
        pass  # the terminal already has enough; a hit counter is not this file's job


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Serve the interface live.")
    parser.add_argument("--host", default=DEFAULT_HOST)
    parser.add_argument("--port", type=int, default=DEFAULT_PORT)
    args = parser.parse_args(argv)

    settings = Settings.from_env()
    configure_logging("WARNING")

    system = build_live_system(settings)
    httpd = LiveServer((args.host, args.port), system)
    print(f"serving http://{args.host}:{args.port}/  (Ctrl+C to stop)")
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        httpd.server_close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
