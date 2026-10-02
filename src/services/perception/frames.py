"""Frames a watch delivers, seen (GM-04 -> GM-03).

A watch that declares ``frames_to="perception"`` hands every picture its
connection delivered - a camera's MJPEG stream, a snapshot operation read on
an interval - to this consumer. It keeps a short rolling sequence per watch
(so pictures read one per tick still make a sequence motion can be seen in),
runs the live perception backend the composition root bound on the ecosystem
over it, and records what is new through the ecosystem's one entry point, so
a condition ("tell me when more than 3 are in view") reacts to a camera the
way it reacts to a thermometer. The latest picture of what was seen is kept
under the ecosystem root, one per watch, for a surface to show.

Track numbers belong to one analysis of the rolling sequence: the count in
view is continuous across windows, a thing's number is not promised to be.
"""

from __future__ import annotations

import logging
import threading
from collections import deque
from datetime import datetime, timedelta
from typing import Any

from src.providers.perception.contract import Frame, PerceptionError
from src.services.automation.tick import register_frame_consumer
from src.services.declared_store import slug
from src.services.perception.analysis import analyse, frames_from_media, observations_of

logger = logging.getLogger(__name__)

MAX_FRAMES = 36
_BUFFERS: "dict[str, deque]" = {}
_WATERMARKS: "dict[str, datetime]" = {}
_LOCK = threading.Lock()


def _pictures(items: list) -> "list[tuple[datetime, bytes, str]]":
    found = []
    for item in items:
        kind = str(getattr(item, "content_type", "") or "").lower()
        payload = getattr(item, "payload", None)
        if not payload:
            continue
        if kind.startswith("video/"):
            try:
                frames = frames_from_media(payload, kind, count=12)
            except PerceptionError:
                continue
            found.extend((item.received_at + timedelta(seconds=f.at_seconds), f.image_bytes, "image/png") for f in frames)
        elif kind.startswith("image/") or not kind or kind == "application/octet-stream":
            found.append((item.received_at, payload, kind or "image/*"))
    return found


def look_path(ecosystem: Any, watch_id: str):
    return ecosystem.root / "looks" / f"{slug(watch_id)}.png"


def perceive_frames(watch: Any, ecosystem: Any, items: list) -> "str | None":
    provider = ecosystem.provider("perception")
    if provider is None:
        return f"{len(items)} frame(s) arrived, but no perception backend is bound"
    pictures = _pictures(items)
    if not pictures:
        return f"{len(items)} item(s) arrived, none of them a picture"
    with _LOCK:
        buffer = _BUFFERS.setdefault(watch.watch_id, deque(maxlen=MAX_FRAMES))
        # Many frames in one listen are thinned evenly to what the buffer holds.
        step = max(1, len(pictures) // MAX_FRAMES)
        buffer.extend(pictures[::step])
        sequence = list(buffer)
        watermark = _WATERMARKS.get(watch.watch_id)
    started = sequence[0][0]
    frames = [Frame((at - started).total_seconds(), data) for at, data, _kind in sequence]
    try:
        report = analyse(frames, provider, source=watch.entity, label_tracks=0)
    except PerceptionError as exc:
        return f"perception could not look at {watch.entity}: {exc}"
    found = [o for o in observations_of(report, started=started, sensitivity=watch.sensitivity)
             if watermark is None or o.observed_at > watermark]
    recorded = ecosystem.observe(found)
    with _LOCK:
        _WATERMARKS[watch.watch_id] = sequence[-1][0]
    if report.picture:
        path = look_path(ecosystem, watch.watch_id)
        try:
            path.parent.mkdir(parents=True, exist_ok=True)
            temporary = path.with_suffix(".tmp")
            temporary.write_bytes(report.picture)
            temporary.replace(path)
        except OSError:
            logger.exception("could not keep the latest look for %s", watch.watch_id)
    return (f"perception ({report.backend}) looked at {len(frames)} frame(s) of {watch.entity}: "
            f"{report.counts[-1][1] if report.counts else 0} in view now, {recorded} observation(s) kept")


def forget_watch(watch_id: str) -> None:
    with _LOCK:
        _BUFFERS.pop(watch_id, None)
        _WATERMARKS.pop(watch_id, None)


register_frame_consumer("perception", perceive_frames)
