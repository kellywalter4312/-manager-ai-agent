"""Serve the interface against the live running system - and now talk to it.

    python scripts/serve_ui.py [--host 127.0.0.1] [--port 8642]
                                [--https] [--tls-host HOST ...]

**Private device access (2026-09-09).** `--https` wraps this exact server -
same routes, same one `LiveSystem`, nothing duplicated - in a locally
generated TLS certificate (`scripts/dev_tls.py`), so a browser reached over
`--host 0.0.0.0` from another device on the same private network gets a
real secure context and can therefore use microphone-dependent capabilities
(Recording, ambient voice activation) that browsers otherwise refuse
outside `localhost`. This is never a public deployment: the certificate is
self-signed, covers only addresses this machine is actually reachable at on
its own network (detected live - see `dev_tls.detect_private_addresses`),
and each device must explicitly trust it once, the same way any internal-
only HTTPS site is trusted on a phone that has never heard of a public CA.
Omitting `--https` leaves this file's behaviour byte-identical to before -
plain HTTP, `localhost`-only by default, exactly as it always was.

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
    POST /recording/started {"agent": "...", "purpose": "..."?} -> the
                         frontend's own acknowledgement that a real
                         browser microphone stream actually opened.
    POST /recording/save {"agent": "...", "purpose": "...", "audio_base64":
                         "...", "mime_type": "...", "duration_seconds": N} ->
                         registers a real, persistent audio artifact
                         (register_new_artifact) and a governed Memory
                         reference (retain_for) - the actual save.
    POST /recording/cancel {"agent": "..."} -> discards an in-progress
                         recording session without saving it.
    POST /recording/failed {"agent": "...", "reason": "..."} -> the
                         frontend's own honest report that capture could
                         not happen (denied permission, unsupported
                         runtime, a genuine MediaRecorder error).
    POST /recording/clear {"agent": "..."} -> returns an agent to the
                         honest READY default once a SAVED/FAILED/
                         CANCELLED session has been acknowledged on screen.
    POST /workspace/enter {"agent": "..."} -> the one real acknowledgement
                         a served page calls once a person has genuinely
                         entered an agent's own 3D Workspace, through
                         record_workspace_entry() -
                         refused for an agent that never declared
                         InterfaceFeature.WORKSPACE.
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
import base64
import json
import secrets
import ssl
import sys
import threading
import time
from dataclasses import dataclass
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from scripts.control_center import (  # noqa: E402
    ConfigurationError,
    ProviderConfigurationError,
    TimezoneError,
    build as assemble_system,
    check_configuration,
    classify_administrative_write,
    run_command,
)
from scripts.exchange import build_recording_record, exchange, memory_for, retain_for  # noqa: E402
from src.services.audit.action_log import DEFAULT_ACTION_LOG_PATH, load_action_log  # noqa: E402
from scripts.export_ui import render  # noqa: E402
from src.config.settings import Settings  # noqa: E402
from src.control_center.interfaces import InterfaceFeature, describe_interface  # noqa: E402
from src.control_center.observer import ControlCenter  # noqa: E402
from src.control_center.render import render_overview  # noqa: E402
from src.control_center.snapshot import snapshot  # noqa: E402
from src.manager.proactive_experience import deliver_experience  # noqa: E402
from src.services.artifacts.models import VoiceRecordingContent  # noqa: E402
from src.services.artifacts.registration import register_new_artifact  # noqa: E402
from src.services.identity.presence import (  # noqa: E402
    PresenceState,
    set_avatar_preference,
    set_digital_human_style,
    set_environment_preference,
)
from src.services.identity.workspace import record_workspace_entry  # noqa: E402
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
    if path == "/status":
        # Foundation Repair #5 (reconciliation audit, 2026-09-05): the
        # Control Center's own `status` view, reachable over the second
        # door this project already has for everything else - `talk`/
        # `message` share exchange(), and this shares render_overview()
        # with the terminal's own `status` command. Not a second summary
        # with its own JSON schema to keep in sync: the identical text a
        # terminal session would see for the identical question.
        return (
            200,
            render_overview(system.centre.overview()).encode("utf-8"),
            "text/plain; charset=utf-8",
        )
    if path == "/actions":
        # The durable counterpart to /status above - what `actions` shows
        # in the terminal, read from the identical store
        # exchange()'s own consequential-action recording writes to.
        entries = load_action_log(DEFAULT_ACTION_LOG_PATH)
        body = (
            "\n".join(entry.describe() for entry in entries[-20:])
            if entries
            else "no consequential actions recorded yet"
        )
        return 200, body.encode("utf-8"), "text/plain; charset=utf-8"
    if path == "/":
        data = _current_snapshot(system)
        return 200, render(data).encode("utf-8"), "text/html; charset=utf-8"
    if path in VENDOR_ASSETS:
        filename, content_type = VENDOR_ASSETS[path]
        return 200, (VENDOR_DIR / filename).read_bytes(), content_type
    if path.startswith("/artifact/"):
        return _route_artifact_file(path, system)
    return 404, b"not found", "text/plain; charset=utf-8"


# Every rendition format an artifact could plausibly have produced so
# far, mapped to its real MIME type - never a closed catalog forcing a
# code change per artifact type: a future rendition extension this
# dict does not yet name still serves, just as an ordinary octet
# stream, rather than being refused.
_RENDITION_CONTENT_TYPES = {
    "pdf": "application/pdf", "png": "image/png", "jpg": "image/jpeg",
    "mp4": "video/mp4", "wav": "audio/wav", "txt": "text/plain; charset=utf-8",
    # Real Audio Recording/Capture checkpoint: every real MediaRecorder
    # container this project's own frontend may produce (see
    # _RECORDING_EXTENSIONS above) - "m4a" rather than "mp4" for a
    # Safari-style AAC-in-MP4 recording specifically so it is never
    # served under the same extension "mp4" already means for video.
    "webm": "audio/webm", "ogg": "audio/ogg", "m4a": "audio/mp4", "mp3": "audio/mpeg",
}


def _route_artifact_file(path: str, system: LiveSystem) -> tuple[int, bytes, str]:
    """Serve one real, already-produced artifact rendition file -
    ``/artifact/<id>.<format>`` for the latest version's rendition in
    that format (a document's "pdf", an image/diagram's "png", or any
    future format a future artifact type produces - this route does not
    hardcode which). Reads the identical, authoritative ArtifactStore
    ``/snapshot.json`` itself reads (``system.runtime.artifacts``) -
    never a second lookup, and never a fabricated response: a missing
    artifact or a version with no rendition in the requested format (no
    rendering/generation engine configured) is an honest 404, not an
    empty or placeholder file.
    """
    remainder = path[len("/artifact/") :]
    artifact_id, sep, extension = remainder.rpartition(".")
    if not sep:
        return 404, b"not found", "text/plain; charset=utf-8"

    artifact = system.runtime.artifacts.get(artifact_id)
    if artifact is None:
        return 404, b"no artifact with that id", "text/plain; charset=utf-8"

    rendition_path = artifact.latest.renditions.get(extension)
    if not rendition_path:
        return 404, f"this artifact has no {extension!r} rendition".encode(), "text/plain; charset=utf-8"

    file_path = Path(rendition_path)
    if not file_path.is_file():
        return 404, b"the rendered file is missing on disk", "text/plain; charset=utf-8"

    content_type = _RENDITION_CONTENT_TYPES.get(extension, "application/octet-stream")
    return 200, file_path.read_bytes(), content_type


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
                addressed=addressed, speak=True, action_log_path=DEFAULT_ACTION_LOG_PATH,
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


def route_experience(record_id: object, system: LiveSystem) -> tuple[int, bytes, str]:
    """Decide what to answer for `POST /experience`. No socket involved.

    General Time/Event Awareness + Proactive Visual Experience
    checkpoint: the one real acknowledgement a served page calls once
    it has genuinely presented a pending experience - `deliver_experience()`
    (`src/manager/proactive_experience.py`), the identical governed
    write path Control Center's own read would otherwise never trigger,
    since presenting something is an action a *frontend* takes, never a
    side effect of merely reading a snapshot.

    Marking something delivered that was never genuinely presented
    would be exactly the "duplicate triggering never actually shown"
    failure requirement 10 refuses - so this route exists at all only
    because the caller (the served page's own render step) is the one
    place that can honestly say the presentation happened.
    """
    if not isinstance(record_id, str) or not record_id.strip():
        return (
            400,
            json.dumps({"error": "record_id must not be empty"}).encode("utf-8"),
            "application/json; charset=utf-8",
        )

    with system.lock:
        delivered = deliver_experience(system.manager, record_id.strip())

    if delivered is None:
        return (
            404,
            json.dumps({"error": f"no such pending experience: {record_id!r}"}).encode("utf-8"),
            "application/json; charset=utf-8",
        )
    return (
        200,
        json.dumps({"record_id": delivered.record_id, "delivered": delivered.delivered}).encode("utf-8"),
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


def route_workspace_enter(agent: object, system: LiveSystem) -> tuple[int, bytes, str]:
    """Decide what to answer for `POST /workspace/enter`. No socket involved.

    The one real acknowledgement a served page calls once a person has
    genuinely entered an agent's own 3D Workspace - never a side effect of
    merely reading a snapshot (GET requests stay unlocked and idempotent),
    the identical "the caller is the only place that can honestly say the
    thing happened" reasoning `route_experience` above already documents
    for its own event.

    Resolves ``agent`` through `memory_for()`, the identical rule every
    other write route in this file already applies - an unrecognised name,
    or one with no Memory, writes nothing. Refuses an agent that has not
    actually declared `InterfaceFeature.WORKSPACE` (see
    `src/control_center/interfaces.py`): entering a Workspace that was never
    offered would be the frontend inventing a capability the agent itself
    never claimed, exactly what `InterfaceDescriptor`'s own docstring calls
    out as a lie a screen must not tell.

    The write itself is `record_workspace_entry()`
    (`src/services/identity/workspace.py`), the identical governed
    forget-then-remember path `set_avatar_preference` already established -
    this route is another caller of it, never a second implementation.
    """
    if not isinstance(agent, str) or not agent.strip():
        return (
            400,
            json.dumps({"error": "agent must not be empty"}).encode("utf-8"),
            "application/json; charset=utf-8",
        )

    manager_name = system.manager.own_skills.name if system.manager.own_skills else None
    resolved = system.manager.own_skills if agent == manager_name else system.manager.registry.get(agent)
    if resolved is None:
        return (
            404,
            json.dumps({"error": f"no such agent: {agent!r}"}).encode("utf-8"),
            "application/json; charset=utf-8",
        )
    interface = describe_interface(resolved)
    if not interface.can_render(InterfaceFeature.WORKSPACE):
        return (
            400,
            json.dumps({"error": f"'{agent}' has no Workspace to enter"}).encode("utf-8"),
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
        session = record_workspace_entry(
            agent, recall=memory.recall, remember=memory.remember, forget=memory.forget
        )

    return (
        200,
        json.dumps({"agent": agent, "workspace": session.to_dict()}).encode("utf-8"),
        "application/json; charset=utf-8",
    )


# Real Audio Recording/Capture: four small routes for the one thing only a
# browser can actually do - capture microphone bytes and hand them over.
# None of these touch a capability, a claims= predicate or the Brain:
# recognising "start recording" in words is a Manager capability's job
# (src/manager/capabilities.py's start_recording/stop_recording/
# cancel_recording), reached through the existing /message route exactly
# like every other natural-language request. These routes are the other
# half - what happens once a real user gesture and real captured bytes
# exist - mirroring route_workspace_enter's own "a UI action the frontend
# must actually perform" shape rather than a conversational one.
#
# Every real MediaRecorder mimeType this project's own frontend requests
# (see ui/index.html's own RECORDING_MIME_CANDIDATES), mapped to the file
# extension _RENDITION_CONTENT_TYPES already knows how to serve back -
# never a closed catalog forcing a code change per browser: an
# unrecognised mime type still saves, under "webm", the most broadly
# supported real container this project can honestly claim.
_RECORDING_EXTENSIONS = {
    "audio/webm": "webm",
    "audio/ogg": "ogg",
    "audio/mp4": "m4a",
    "audio/aac": "m4a",
    "audio/wav": "wav",
    "audio/x-wav": "wav",
    "audio/mpeg": "mp3",
}


def _recording_extension(mime_type: object) -> str:
    base = str(mime_type or "").split(";")[0].strip().lower()
    return _RECORDING_EXTENSIONS.get(base, "webm")


def route_recording_started(agent: object, purpose: object, system: LiveSystem) -> tuple[int, bytes, str]:
    """Decide what to answer for `POST /recording/started`. No socket
    involved.

    The one real acknowledgement a served page calls once a genuine user
    gesture has actually opened a browser microphone stream - never
    called speculatively, and never a claim this route itself verifies
    (it cannot reach into a browser to check; it takes the caller's word
    for the same reason `route_experience` already does for "this was
    genuinely presented"). Works whether or not a natural-language
    "start recording" preceded it - see
    `RecordingSessionRegistry.mark_recording`'s own docstring for why a
    person pressing a real Start control with nothing said first must
    not be refused.
    """
    if not isinstance(agent, str) or not agent.strip():
        return (
            400,
            json.dumps({"error": "agent must not be empty"}).encode("utf-8"),
            "application/json; charset=utf-8",
        )
    if memory_for(system.manager, agent) is None:
        return (
            404,
            json.dumps({"error": f"no such agent: {agent!r}"}).encode("utf-8"),
            "application/json; charset=utf-8",
        )
    purpose_value = str(purpose).strip() if isinstance(purpose, str) and purpose.strip() else None
    with system.lock:
        session = system.runtime.recording_sessions.mark_recording(agent, purpose_value)
    return (
        200,
        json.dumps({"agent": agent, "session": session.to_dict()}).encode("utf-8"),
        "application/json; charset=utf-8",
    )


def route_recording_save(
    agent: object, purpose: object, audio_base64: object, mime_type: object,
    duration_seconds: object, system: LiveSystem,
) -> tuple[int, bytes, str]:
    """Decide what to answer for `POST /recording/save`. No socket
    involved.

    The one real, governed write for a finished recording - a brand-new
    artifact (`register_new_artifact`, the identical shared helper every
    other real, produced artifact in this project already uses to write
    content.json plus one real rendition) and, alongside it, a real
    Memory reference under `MemoryCategory.AUDIO_RECORDING`, through
    `retain_for`'s own governed path - never the raw audio itself, which
    stays exactly where the artifact store already keeps every other
    media artifact's bytes.

    The artifact write is the source of truth for "this recording is
    genuinely saved" - a Memory-policy refusal (vanishingly unlikely for
    a short, generated description) still leaves the real audio
    retrievable through the artifact store, honestly reported rather
    than silently discarding a person's real recording over a metadata
    technicality.
    """
    if not isinstance(agent, str) or not agent.strip():
        return (
            400,
            json.dumps({"error": "agent must not be empty"}).encode("utf-8"),
            "application/json; charset=utf-8",
        )
    if not isinstance(audio_base64, str) or not audio_base64.strip():
        return (
            400,
            json.dumps({"error": "audio_base64 must not be empty"}).encode("utf-8"),
            "application/json; charset=utf-8",
        )
    memory = memory_for(system.manager, agent)
    if memory is None:
        return (
            404,
            json.dumps({"error": f"no such agent: {agent!r}"}).encode("utf-8"),
            "application/json; charset=utf-8",
        )
    retain = retain_for(system.manager, agent)
    if retain is None:
        return (
            404,
            json.dumps({"error": f"'{agent}' has no governed way to retain a recording"}).encode("utf-8"),
            "application/json; charset=utf-8",
        )
    try:
        audio_bytes = base64.b64decode(audio_base64, validate=True)
    except (ValueError, TypeError):
        return (
            400,
            json.dumps({"error": "audio_base64 is not valid base64"}).encode("utf-8"),
            "application/json; charset=utf-8",
        )
    if not audio_bytes:
        return (
            400,
            json.dumps({"error": "the recording is empty - nothing was captured"}).encode("utf-8"),
            "application/json; charset=utf-8",
        )

    purpose_value = str(purpose).strip() if isinstance(purpose, str) and purpose.strip() else "general"
    extension = _recording_extension(mime_type)
    duration_value = duration_seconds if isinstance(duration_seconds, (int, float)) else None

    with system.lock:
        system.runtime.recording_sessions.mark_saving(agent)
        artifact = register_new_artifact(
            system.runtime.artifacts,
            type="voice_recording",
            owner_agent=agent,
            title=f"Voice recording ({purpose_value})",
            content_bytes=VoiceRecordingContent(purpose=purpose_value).to_json().encode("utf-8"),
            rendition_format=extension,
            rendition_bytes=audio_bytes,
        )
        record = build_recording_record(
            purpose=purpose_value,
            artifact_id=artifact.artifact_id,
            created_by=agent,
            duration_seconds=duration_value,
            mime_type=str(mime_type or ""),
            rendition_format=extension,
        )
        verdict = retain(record)
        session = system.runtime.recording_sessions.mark_saved(agent, artifact.artifact_id)

    return (
        200,
        json.dumps(
            {
                "agent": agent,
                "artifact_id": artifact.artifact_id,
                "rendition_format": extension,
                "memory_saved": bool(verdict.allowed),
                "memory_reason": verdict.reason if not verdict.allowed else "",
                "session": session.to_dict(),
            }
        ).encode("utf-8"),
        "application/json; charset=utf-8",
    )


def route_recording_cancel(agent: object, system: LiveSystem) -> tuple[int, bytes, str]:
    """Decide what to answer for `POST /recording/cancel`. No socket
    involved. Discards an in-progress session without saving - refuses
    honestly (400) when nothing was in progress, rather than reporting a
    cancellation that did not happen."""
    if not isinstance(agent, str) or not agent.strip():
        return (
            400,
            json.dumps({"error": "agent must not be empty"}).encode("utf-8"),
            "application/json; charset=utf-8",
        )
    with system.lock:
        cancelled = system.runtime.recording_sessions.cancel(agent)
    if not cancelled:
        return (
            400,
            json.dumps({"error": "there is no recording in progress to cancel"}).encode("utf-8"),
            "application/json; charset=utf-8",
        )
    return (
        200,
        json.dumps({"agent": agent, "cancelled": True}).encode("utf-8"),
        "application/json; charset=utf-8",
    )


def route_recording_failed(agent: object, reason: object, system: LiveSystem) -> tuple[int, bytes, str]:
    """Decide what to answer for `POST /recording/failed`. No socket
    involved. The frontend's own honest report that capture could not
    happen - a denied microphone permission, an unsupported runtime, a
    genuine MediaRecorder error - so the session reads FAILED with the
    real reason rather than silently reverting to READY with nothing
    explained."""
    if not isinstance(agent, str) or not agent.strip():
        return (
            400,
            json.dumps({"error": "agent must not be empty"}).encode("utf-8"),
            "application/json; charset=utf-8",
        )
    reason_text = str(reason).strip() if isinstance(reason, str) and reason.strip() else "recording failed"
    with system.lock:
        session = system.runtime.recording_sessions.mark_failed(agent, reason_text)
    return (
        200,
        json.dumps({"agent": agent, "session": session.to_dict()}).encode("utf-8"),
        "application/json; charset=utf-8",
    )


def route_recording_clear(agent: object, system: LiveSystem) -> tuple[int, bytes, str]:
    """Decide what to answer for `POST /recording/clear`. No socket
    involved. Returns this agent to the honest READY default once a
    SAVED/FAILED/CANCELLED session has genuinely been acknowledged on
    screen - see `RecordingSessionRegistry.clear`'s own docstring."""
    if not isinstance(agent, str) or not agent.strip():
        return (
            400,
            json.dumps({"error": "agent must not be empty"}).encode("utf-8"),
            "application/json; charset=utf-8",
        )
    with system.lock:
        system.runtime.recording_sessions.clear(agent)
    return (
        200,
        json.dumps({"agent": agent, "cleared": True}).encode("utf-8"),
        "application/json; charset=utf-8",
    )


# Foundation Repair #5, part 2 (reconciliation audit, 2026-09-05): the
# management verbs run_command() already handles for a terminal, minus
# "talk"/free text. That one stays exclusively /message's - a second way to
# have a conversation would be a second implementation of the one thing
# exchange() exists to be the only door to (see route_message's own
# docstring). Everything below is administrative: provider/credential
# selection, connections, capabilities, voice, ecosystem, avatar/environment/
# profile by verb instead of by dedicated route, status, and durable history -
# exactly the surface an operator already has at a terminal, offered through
# this project's other door.
MANAGEMENT_VERBS = frozenset(
    {
        "status", "history", "actions", "unresolved", "avatar", "environment",
        "profile", "voice", "review", "capabilities", "provider", "connections",
        "services", "sites", "ecosystem", "tasks", "artifacts", "personal", "calendar",
        "experience", "workspace", "recordings",
    }
)

# No real terminal is attached to an HTTP request, so there is no width to
# ask for - a fixed, reasonable column count, the same role a default would
# play if run_command's own columns parameter had one.
COMMAND_COLUMNS = 100


# ---------------------------------------------------------------------------
# Control Center repair item #3 (2026-09-07): confirmation for
# administrative writes.
#
# Not the conversational PendingConfirmation/ActivitySessions machinery
# (src/manager/guided_interaction.py) - that mechanism is tied to a
# multi-turn Conversation and a Brain's own yes/no judgment, built for a
# fundamentally different shape of interaction (a person talking, across
# turns) than a stateless administrative POST. This is the same
# *principle* - nothing consequential happens without an explicit second,
# confirming step - applied to the shape /command actually has: describe,
# then require an exact, single-use, short-lived token back before
# executing. Enforced here, server-side, so a direct API call (curl,
# a script) cannot skip it by simply omitting a UI dialog - the dialog is
# not the governance boundary, this is.
#
# Scope, stated once: only the administrative-write examples the repair
# report actually named - provider add/select/revoke, connections
# connect/disconnect, capabilities enable/disable, and ecosystem's own
# approving run/rollback. Read-only verbs, and the pre-existing
# avatar/environment/profile/voice preference-setting verbs, are
# unaffected - those are cosmetic preferences, not the credential/
# provider/capability/evolution surface this item is about.
_PENDING_CONFIRMATIONS: "dict[str, tuple[str, float]]" = {}
_CONFIRMATION_TTL_SECONDS = 120


def _sweep_expired_confirmations(now: float) -> None:
    expired = [token for token, (_, expires_at) in _PENDING_CONFIRMATIONS.items() if now > expires_at]
    for token in expired:
        _PENDING_CONFIRMATIONS.pop(token, None)


def _issue_confirmation_token(command: str) -> str:
    now = time.monotonic()
    _sweep_expired_confirmations(now)
    token = secrets.token_urlsafe(16)
    _PENDING_CONFIRMATIONS[token] = (command, now + _CONFIRMATION_TTL_SECONDS)
    return token


def _consume_confirmation_token(token: object, command: str) -> bool:
    """True only if `token` is real, unexpired, single-use, and was
    issued for this *exact* command string - never a token from a
    different write, and never usable twice."""
    if not isinstance(token, str) or not token:
        return False
    entry = _PENDING_CONFIRMATIONS.pop(token, None)
    if entry is None:
        return False
    pending_command, expires_at = entry
    return time.monotonic() <= expires_at and pending_command == command


def route_command(
    command: object,
    confirm_token: object,
    system: LiveSystem,
    *,
    action_log_path: "str | Path | None" = None,
) -> tuple[int, bytes, str]:
    """Decide what to answer for `POST /command` - administrative Control
    Center commands, reachable over this project's second door.

    Reuses `run_command` verbatim - the identical function
    `scripts/control_center.py`'s own terminal loop calls for every one of
    these verbs, against the identical `ControlCenter`/`CredentialStore`/
    `EcosystemRegistry` a terminal session reads and writes. Nothing here is
    a second provider-management implementation, a second capability
    registry reader, or a second credential store - see
    `run_provider_command`'s own docstring for why an account added this
    way is exactly as real, and exactly as unable to echo a secret back
    (`ProviderAccount.describe()` never carries one), as one added from a
    terminal.

    Restricted to `MANAGEMENT_VERBS`: "talk" and free text are refused here
    with a clear pointer to `/message`, so this never becomes a second way
    to hold a conversation.

    Administrative writes (see `classify_administrative_write`) never
    execute on this call - the first call always returns a description and
    a fresh token; only a second call carrying that exact token, for that
    exact command, executes. Everything else (every read-only view, and
    every pre-existing preference-setting verb) executes immediately,
    exactly as before this item existed.

    ``action_log_path``, when given, is passed straight through to
    ``run_command`` - the one real place that both classifies and records
    an administrative write (see its own docstring), reused here rather
    than a second recording call at this layer, so a command run from a
    terminal and the identical command run from here are recorded by the
    identical code, once. Never recorded for the earlier "pending" return
    above, so a cancelled write leaves no record. None (the default)
    writes nothing - the identical opt-in shape ``scripts/exchange.py``'s
    own ``action_log_path`` parameter already has. Production callers
    pass the real, shared default path explicitly.
    """
    if not isinstance(command, str) or not command.strip():
        return (
            400,
            json.dumps({"error": "'command' must be a non-empty string"}).encode("utf-8"),
            "application/json; charset=utf-8",
        )

    normalised = command.strip()
    verb = normalised.split(None, 1)[0].lower()
    if verb not in MANAGEMENT_VERBS:
        return (
            400,
            json.dumps(
                {
                    "error": (
                        f"'{verb}' is not a management command - use POST /message "
                        "to hold a conversation instead"
                    ),
                    "management_commands": sorted(MANAGEMENT_VERBS),
                }
            ).encode("utf-8"),
            "application/json; charset=utf-8",
        )

    with system.lock:
        requires_confirmation, description = classify_administrative_write(normalised)
        if requires_confirmation and not _consume_confirmation_token(confirm_token, normalised):
            token = _issue_confirmation_token(normalised)
            return (
                200,
                json.dumps(
                    {
                        "requires_confirmation": True,
                        "description": description,
                        "confirm_token": token,
                    }
                ).encode("utf-8"),
                "application/json; charset=utf-8",
            )

        result = run_command(
            system.centre, normalised, COMMAND_COLUMNS, system.channel, system.conversation,
            action_log_path=action_log_path,
        )

    if requires_confirmation:
        return (
            200,
            json.dumps({"requires_confirmation": False, "result": result}).encode("utf-8"),
            "application/json; charset=utf-8",
        )
    return 200, result.encode("utf-8"), "text/plain; charset=utf-8"


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
        if self.path not in (
            "/message",
            "/avatar",
            "/profile",
            "/environment",
            "/command",
            "/experience",
            "/workspace/enter",
            "/recording/started",
            "/recording/save",
            "/recording/cancel",
            "/recording/failed",
            "/recording/clear",
        ):
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
        elif self.path == "/command":
            command = payload.get("command") if isinstance(payload, dict) else None
            confirm_token = payload.get("confirm_token") if isinstance(payload, dict) else None
            status, body, content_type = route_command(
                command, confirm_token, self.server.system, action_log_path=DEFAULT_ACTION_LOG_PATH
            )
        elif self.path == "/experience":
            record_id = payload.get("record_id") if isinstance(payload, dict) else None
            status, body, content_type = route_experience(record_id, self.server.system)
        elif self.path == "/workspace/enter":
            agent = payload.get("agent") if isinstance(payload, dict) else None
            status, body, content_type = route_workspace_enter(agent, self.server.system)
        elif self.path == "/recording/started":
            agent = payload.get("agent") if isinstance(payload, dict) else None
            purpose = payload.get("purpose") if isinstance(payload, dict) else None
            status, body, content_type = route_recording_started(agent, purpose, self.server.system)
        elif self.path == "/recording/save":
            agent = payload.get("agent") if isinstance(payload, dict) else None
            purpose = payload.get("purpose") if isinstance(payload, dict) else None
            audio_base64 = payload.get("audio_base64") if isinstance(payload, dict) else None
            mime_type = payload.get("mime_type") if isinstance(payload, dict) else None
            duration_seconds = payload.get("duration_seconds") if isinstance(payload, dict) else None
            status, body, content_type = route_recording_save(
                agent, purpose, audio_base64, mime_type, duration_seconds, self.server.system
            )
        elif self.path == "/recording/cancel":
            agent = payload.get("agent") if isinstance(payload, dict) else None
            status, body, content_type = route_recording_cancel(agent, self.server.system)
        elif self.path == "/recording/failed":
            agent = payload.get("agent") if isinstance(payload, dict) else None
            reason = payload.get("reason") if isinstance(payload, dict) else None
            status, body, content_type = route_recording_failed(agent, reason, self.server.system)
        elif self.path == "/recording/clear":
            agent = payload.get("agent") if isinstance(payload, dict) else None
            status, body, content_type = route_recording_clear(agent, self.server.system)
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
    parser.add_argument(
        "--https", action="store_true",
        help=(
            "wrap this same server in a private, locally-generated TLS "
            "certificate (see scripts/dev_tls.py) - never a public one - so "
            "another device on this machine's own private network can use "
            "microphone-dependent capabilities, which browsers otherwise "
            "refuse outside a secure context. Omit to keep serving plain "
            "HTTP, exactly as before."
        ),
    )
    parser.add_argument(
        "--tls-host", action="append", default=[], metavar="HOST",
        help=(
            "an extra hostname or IP to cover in the --https certificate, "
            "beyond what is auto-detected from this machine's own network "
            "right now (e.g. a router-assigned static IP or LAN DNS name). "
            "May be given more than once. Only meaningful with --https."
        ),
    )
    args = parser.parse_args(argv)

    settings = Settings.from_env()
    configure_logging("WARNING")

    try:
        check_configuration(settings)
        system = build_live_system(settings)
    except (ConfigurationError, ProviderConfigurationError, TimezoneError) as exc:
        # Final comprehensive audit (2026-09-08): the identical guard
        # every other real entry point (main.py, control_center.py,
        # heartbeat.py) already runs before building anything - found
        # missing here, the one entry point that stays running longest.
        # A server that started anyway on an incoherent configuration
        # would keep answering requests from a system that should never
        # have been built.
        print(f"configuration error: {exc}")
        return 2

    httpd = LiveServer((args.host, args.port), system)
    scheme = "http"
    if args.https:
        # Private device-access checkpoint (2026-09-09): a transport
        # decision made here, in main(), never inside LiveServer itself -
        # LiveServer still stores exactly the one `system` attribute the
        # architecture guard in tests/test_live_ui.py already requires.
        # Lazily imported so a plain-HTTP run (still the default) never
        # even attempts to import the optional 'cryptography' dependency
        # this needs.
        from scripts.dev_tls import TLSUnavailable, build_ssl_context, describe_trust_steps, ensure_certificate

        try:
            info = ensure_certificate(extra_hosts=tuple(args.tls_host))
            httpd.socket = build_ssl_context(info).wrap_socket(httpd.socket, server_side=True)
        except TLSUnavailable as exc:
            # The same honest-refusal shape as the configuration guard
            # above: no fallback to plain HTTP, which would silently
            # hand back a microphone-refusing origin while claiming
            # --https had been honoured.
            print(f"configuration error: {exc}")
            httpd.server_close()
            return 2
        scheme = "https"
        print(describe_trust_steps(info))

    print(f"serving {scheme}://{args.host}:{args.port}/  (Ctrl+C to stop)")
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        httpd.server_close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
