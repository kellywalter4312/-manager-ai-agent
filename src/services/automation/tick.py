"""The ecosystem tick - the one scheduled step that runs whatever is due.

Called by every wake this system already has: the heartbeat (``--once`` from
an OS scheduler, or ``--watch``), the live server's own background ticker
while the page is served, and the Control Center's ``tick`` command. Each call
runs the registered steps once, in order, and reports what each did; nothing
loops here and nothing sleeps, so a crash costs one tick, never the schedule
(the same reasoning ``src/heartbeat/runner.py`` gives for its own wake).

**Steps are an open registry.** Sampling due watches (GM-01/GM-04) and
evaluating conditions (GM-02, which is how silence is ever noticed) are the
built-ins; a mechanism that has something due on a schedule - supervising
long-running device work (GM-11), retiring captures past their retention
(GM-12), purging information past its sensitivity retention (GM-13) -
registers its own step and every wake runs it, with nothing here changing.
"""

from __future__ import annotations

import logging
import threading
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Callable

from src.services.observations.store import utc_now
from src.services.observations.watches import sample

logger = logging.getLogger(__name__)

TickStep = Callable[[Any, Any, datetime], "str | None"]

_STEPS: "dict[str, TickStep]" = {}
_TICK_LOCK = threading.Lock()


def register_tick_step(name: str, step: TickStep) -> None:
    """``step(ecosystem, connections, now)`` returns a one-line account of
    what it did (or None for nothing)."""
    _STEPS[str(name)] = step


def tick_steps() -> "tuple[str, ...]":
    return tuple(_STEPS)


@dataclass
class TickReport:
    at: datetime
    lines: "list[str]" = field(default_factory=list)
    failures: "list[str]" = field(default_factory=list)

    def describe(self) -> str:
        head = f"ecosystem tick at {self.at:%Y-%m-%d %H:%M:%S} UTC"
        body = [f"  {line}" for line in self.lines] or ["  nothing was due"]
        tail = [f"  FAILED {line}" for line in self.failures]
        return "\n".join([head, *body, *tail])


def _connection(connections: Any, name: str) -> Any:
    if connections is None:
        return None
    found = connections.get(name)
    return found if found is not None and found.name == name else None


# GM-04 -> GM-03: what a frame arriving on a watch is handed to, by the name
# the watch declared in ``frames_to`` ("perception", ...). Registered by the
# mechanism that consumes frames; the tick never knows what one is.
FrameConsumer = Callable[[Any, Any, list], "str | None"]
_FRAME_CONSUMERS: "dict[str, FrameConsumer]" = {}
# Stream watches a live listener is already reading continuously - the tick
# leaves them to it rather than opening a second listen.
_LISTENED: "set[str]" = set()
_LISTENED_LOCK = threading.Lock()


def register_frame_consumer(name: str, consumer: FrameConsumer) -> None:
    """``consumer(watch, ecosystem, frames)`` receives the frames (``StreamItem``
    with a payload) a watch declaring ``frames_to=name`` delivered."""
    _FRAME_CONSUMERS[str(name)] = consumer


def hand_over_frames(watch: Any, ecosystem: Any, frames: list) -> "str | None":
    if not frames or not getattr(watch, "frames_to", ""):
        return None
    consumer = _FRAME_CONSUMERS.get(watch.frames_to)
    if consumer is None:
        return f"{len(frames)} frame(s) for '{watch.frames_to}', which is not installed"
    return consumer(watch, ecosystem, list(frames))


def _watches_step(ecosystem: Any, connections: Any, now: datetime) -> "str | None":
    outcomes = []
    handed_over = []
    with _LISTENED_LOCK:
        listened = set(_LISTENED)
    for watch in ecosystem.watches.due(now):
        if watch.watch_id in listened:
            continue
        outcome = sample(watch, _connection(connections, watch.connection), ecosystem.observe, now=now)
        ecosystem.watches.note(outcome, now)
        handed = hand_over_frames(watch, ecosystem, list(outcome.frames))
        if handed:
            logger.info("frames from %s: %s", watch.watch_id, handed)
            handed_over.append(f"{watch.watch_id}: {handed}")
        outcomes.append(outcome)
    if not outcomes:
        return None
    ok = sum(1 for o in outcomes if o.ok)
    recorded = sum(o.recorded for o in outcomes)
    failed = [f"{o.watch_id} ({o.detail})" for o in outcomes if not o.ok]
    return (f"watches: {ok}/{len(outcomes)} read, {recorded} observation(s) kept"
            + (f"; not read: {', '.join(failed)}" if failed else "")
            + (f"; frames: {'; '.join(handed_over)}" if handed_over else ""))


def _conditions_step(ecosystem: Any, connections: Any, now: datetime) -> "str | None":
    # Events are reported once, by the tick, whichever step made them fire
    # (a watch's arrival or this evaluation) - see run_ecosystem_tick.
    ecosystem.engine.evaluate_all(now)
    return None


register_tick_step("watches", _watches_step)
register_tick_step("conditions", _conditions_step)


def run_ecosystem_tick(ecosystem: Any, connections: Any, *, now: "datetime | None" = None) -> TickReport:
    """Run every registered step once. One tick at a time per process, so the
    live server's ticker and a Control Center ``tick`` never interleave."""
    now = now or utc_now()
    report = TickReport(at=now)
    if ecosystem is None:
        report.failures.append("no ecosystem state in this runtime")
        return report
    fired: list = []
    engine = getattr(ecosystem, "engine", None)
    if engine is not None:
        engine.subscribe(fired.append)
    try:
        with _TICK_LOCK:
            for name, step in list(_STEPS.items()):
                try:
                    line = step(ecosystem, connections, now)
                except Exception as exc:  # noqa: BLE001 - one step never stops the rest
                    logger.exception("ecosystem tick step '%s' failed", name)
                    report.failures.append(f"{name}: {type(exc).__name__}: {exc}")
                    continue
                if line:
                    report.lines.append(line)
    finally:
        if engine is not None:
            engine.unsubscribe(fired.append)
    if fired:
        report.lines.append("conditions: " + "; ".join(f"{e.label} -> {e.detail}" for e in fired))
    return report


class StreamListeners:
    """GM-04: while a live server runs, every enabled stream watch is read
    continuously - one bounded listen after another on its own thread - so a
    device that pushes events or frames is heard as it speaks, not only when a
    tick comes round. ``refresh()`` starts listeners for new stream watches and
    lets those removed or paused end; the ticker calls it every tick."""

    def __init__(self, runtime: "Callable[[], Any]", *, gap: float = 0.5) -> None:
        self._runtime = runtime
        self._gap = gap
        self._threads: "dict[str, threading.Thread]" = {}
        self._stop = threading.Event()

    def refresh(self) -> "list[str]":
        runtime = self._runtime()
        ecosystem = getattr(runtime, "ecosystem", None)
        connections = getattr(runtime, "connections", None)
        if ecosystem is None:
            return []
        started = []
        for watch in ecosystem.watches.all():
            connection = _connection(connections, watch.connection)
            streaming = watch.enabled and connection is not None and connection.supports_streaming(watch.operation)
            alive = watch.watch_id in self._threads and self._threads[watch.watch_id].is_alive()
            if streaming and not alive:
                thread = threading.Thread(target=self._listen, args=(watch.watch_id,), daemon=True,
                                          name=f"stream-{watch.watch_id}")
                self._threads[watch.watch_id] = thread
                with _LISTENED_LOCK:
                    _LISTENED.add(watch.watch_id)
                thread.start()
                started.append(watch.watch_id)
        return started

    def _listen(self, watch_id: str) -> None:
        try:
            while not self._stop.is_set():
                runtime = self._runtime()
                ecosystem = getattr(runtime, "ecosystem", None)
                watch = ecosystem.watches.get(watch_id) if ecosystem is not None else None
                if watch is None or not watch.enabled:
                    return
                connection = _connection(getattr(runtime, "connections", None), watch.connection)
                if connection is None:
                    return
                now = utc_now()
                outcome = sample(watch, connection, ecosystem.observe, now=now)
                ecosystem.watches.note(outcome, now)
                hand_over_frames(watch, ecosystem, list(outcome.frames))
                if self._stop.wait(self._gap if outcome.ok else max(self._gap, 5.0)):
                    return
        except Exception:  # noqa: BLE001 - one listener's failure ends only itself
            logger.exception("stream listener '%s' stopped", watch_id)
        finally:
            with _LISTENED_LOCK:
                _LISTENED.discard(watch_id)

    def stop(self) -> None:
        self._stop.set()


class BackgroundTicker:
    """Runs the ecosystem tick every ``interval`` seconds on a daemon thread
    for as long as a live server runs - so a watch keeps sampling and a
    condition keeps being checked while the page is open, without an OS
    scheduler. ``runtime`` is a callable, read on every tick, so a live
    backend rebind (new connections) is used at once."""

    def __init__(self, runtime: "Callable[[], Any]", *, interval: float = 15.0,
                 on_report: "Callable[[TickReport], Any] | None" = None, listen: bool = True) -> None:
        self._runtime = runtime
        self._interval = max(1.0, float(interval))
        self._on_report = on_report
        self._stop = threading.Event()
        self._thread: "threading.Thread | None" = None
        self.listeners = StreamListeners(runtime) if listen else None

    def start(self) -> "BackgroundTicker":
        if self._thread is None:
            if self.listeners is not None:
                self.listeners.refresh()
            self._thread = threading.Thread(target=self._loop, name="ecosystem-tick", daemon=True)
            self._thread.start()
        return self

    def stop(self) -> None:
        self._stop.set()
        if self.listeners is not None:
            self.listeners.stop()

    def _loop(self) -> None:
        while not self._stop.wait(self._interval):
            try:
                if self.listeners is not None:
                    self.listeners.refresh()
                runtime = self._runtime()
                report = run_ecosystem_tick(getattr(runtime, "ecosystem", None), getattr(runtime, "connections", None))
                if self._on_report is not None and (report.lines or report.failures):
                    self._on_report(report)
            except Exception:  # noqa: BLE001 - the ticker outlives any one bad tick
                logger.exception("background ecosystem tick failed")
