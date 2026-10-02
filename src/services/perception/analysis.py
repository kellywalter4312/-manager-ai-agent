"""From regions to an account of what happened (GM-03).

``analyse`` runs a perception backend over frames, follows what it found
across them (``tracking.track``), names each tracked thing once when the
backend can, and reports - as data and in plain words - how many things there
were, where each went, when each arrived, left or stopped. It also records
what was seen through the ecosystem's one entry point (``observe``): the
count at each moment and each tracked thing's position, so a condition
("tell me when more than 5 are in view"), a zone test (GM-05) or a dashboard
(GM-06) works on a video, a live camera or a sensor alike.

**Honest about what it cannot say.** A backend that finds regions without
naming them yields "things" and "moving regions", never a guessed noun; a
person who asked "how many cows" hears how many distinct moving things were
seen and that nothing here confirmed what they are.
"""

from __future__ import annotations

import io
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from typing import Any, Sequence

from src.providers.perception.contract import Frame, FramePerception, PerceptionError, PerceptionProvider
from src.services.observations.store import Observation
from src.services.perception.tracking import Track, track

MAX_LABELS = 3


@dataclass
class PerceptionReport:
    source: str
    backend: str
    frames: int
    span_seconds: float
    counts: "list[tuple[float, int]]"
    tracks: "list[Track]"
    events: "list[dict]" = field(default_factory=list)
    labelled: bool = False
    label_notes: "list[str]" = field(default_factory=list)
    picture: "bytes | None" = None
    glimpses: int = 0

    @property
    def peak(self) -> int:
        return max((n for _t, n in self.counts), default=0)

    def to_dict(self) -> dict:
        return {
            "source": self.source, "backend": self.backend, "frames": self.frames,
            "span_seconds": round(self.span_seconds, 3), "peak_in_view": self.peak,
            "distinct": len(self.tracks), "counts": [[round(t, 3), n] for t, n in self.counts],
            "tracks": [{
                "id": t.track_id, "label": t.label, "first_at": round(t.first_at, 3), "last_at": round(t.last_at, 3),
                "moved": t.moved, "heading": t.heading(), "travelled": round(t.travelled, 3),
                "path": [[round(x, 4), round(y, 4)] for x, y in t.centres],
            } for t in self.tracks],
            "events": self.events, "labelled": self.labelled, "label_notes": self.label_notes,
            "glimpses": self.glimpses,
        }

    def describe(self, *, asked_about: str = "") -> str:
        """A plain account - Half Brain, no model."""
        if not self.tracks:
            if self.frames and self.backend == "motion":
                return (f"Across {self.frames} frame(s) of {self.source}, nothing moved against the background "
                        "- no moving thing was seen.")
            return f"Across {self.frames} frame(s) of {self.source}, nothing stood out to track."
        noun = "thing"
        lines = [
            f"Across {self.frames} frame(s) of {self.source} ({self.span_seconds:.1f}s), I found "
            f"{len(self.tracks)} distinct {noun}{'s' if len(self.tracks) != 1 else ''}, at most {self.peak} in view at once."
        ]
        for t in self.tracks[:12]:
            name = f" ({t.label})" if t.label else ""
            lines.append(f"  #{t.track_id}{name}: seen {t.first_at:.1f}s-{t.last_at:.1f}s, {t.heading()}")
        if len(self.tracks) > 12:
            lines.append(f"  ... and {len(self.tracks) - 12} more")
        for event in self.events[:16]:
            lines.append(f"  {event['at']:.1f}s: #{event['track']} {event['what']}")
        if self.glimpses:
            lines.append(f"  (and {self.glimpses} brief glimpse(s) seen in a single frame, not followed)")
        if not self.labelled:
            what = f" whether they are {asked_about}" if asked_about else " what they are"
            lines.append(f"This backend ({self.backend}) finds where things are, not{what} - "
                         "nothing here confirmed that.")
        lines.extend(self.label_notes)
        return "\n".join(lines)


def _events(tracks: "list[Track]", first: float, last: float) -> "list[dict]":
    events = []
    for t in tracks:
        if t.first_at > first + 1e-6:
            events.append({"at": t.first_at, "track": t.track_id, "what": "came into view"})
        for begin, end in t.still_spans():
            if begin > t.first_at + 1e-6 or t.first_at > first + 1e-6:
                events.append({"at": begin, "track": t.track_id, "what": "stopped"})
            if end < t.last_at - 1e-6:
                events.append({"at": end, "track": t.track_id, "what": "moved off"})
        if t.last_at < last - 1e-6:
            events.append({"at": t.last_at, "track": t.track_id, "what": "left view"})
    return sorted(events, key=lambda e: (e["at"], e["track"]))


def picture(frames: Sequence[Frame], perceptions: Sequence[FramePerception], tracks: "list[Track]") -> "bytes | None":
    """One picture of what was seen: the last frame, each track's path drawn
    and its last box outlined, numbered as the account numbers it."""
    if not frames:
        return None
    from PIL import ImageDraw

    from src.providers.perception.contract import open_image

    image = open_image(frames[-1].image_bytes)
    draw = ImageDraw.Draw(image)
    width, height = image.size
    palette = [(230, 57, 70), (29, 53, 87), (42, 157, 143), (244, 162, 97), (131, 56, 236), (0, 150, 199)]
    for t in tracks:
        colour = palette[(t.track_id - 1) % len(palette)]
        path = [(x * width, y * height) for x, y in t.centres]
        if len(path) > 1:
            draw.line(path, fill=colour, width=max(2, width // 320))
        x0, y0, x1, y1 = t.last.box
        draw.rectangle([x0 * width, y0 * height, x1 * width, y1 * height], outline=colour, width=max(2, width // 320))
        draw.text((x0 * width + 3, y0 * height + 2), f"#{t.track_id}{(' ' + t.label) if t.label else ''}", fill=colour)
    buffer = io.BytesIO()
    image.save(buffer, "PNG")
    return buffer.getvalue()


def analyse(
    frames: Sequence[Frame],
    provider: PerceptionProvider,
    *,
    source: str,
    label_tracks: int = MAX_LABELS,
    draw: bool = True,
) -> PerceptionReport:
    if not frames:
        raise PerceptionError("there is nothing to look at")
    perceptions = provider.perceive(frames)
    followed = track(perceptions)
    # A thing seen in a single frame is a glimpse, not something followed -
    # counted, never presented as a tracked thing.
    tracks = [t for t in followed if len(t.points) >= 2 or len(frames) < 3]
    glimpses = len(followed) - len(tracks)
    for number, followed_thing in enumerate(tracks, 1):
        followed_thing.track_id = number  # numbered as the account presents them
    report = PerceptionReport(
        source=source, backend=provider.name, frames=len(frames),
        span_seconds=frames[-1].at_seconds - frames[0].at_seconds,
        counts=[(p.at_seconds, len(p.detections)) for p in perceptions], tracks=tracks,
        events=_events(tracks, frames[0].at_seconds, frames[-1].at_seconds),
        glimpses=glimpses,
    )
    labeller = getattr(provider, "label", None)
    if provider.labels_objects and labeller is not None and tracks and label_tracks > 0:
        by_time = {f.at_seconds: f for f in frames}
        for t in sorted(tracks, key=lambda t: -len(t.points))[:label_tracks]:
            at, detection = t.best()
            try:
                t.label = labeller(by_time[at].image_bytes, detection.box)
            except PerceptionError as exc:
                report.label_notes.append(f"Naming stopped: {exc}.")
                break
        report.labelled = any(t.label for t in tracks)
        if len(tracks) > label_tracks:
            report.label_notes.append(f"I named the {label_tracks} longest-seen things only - naming is slow here.")
    if draw:
        report.picture = picture(frames, perceptions, tracks)
    return report


def observations_of(report: PerceptionReport, *, started: datetime, sensitivity: str = "") -> "list[Observation]":
    """What was seen, as observations: the count in view at each moment, and
    each tracked thing's position (in the picture's own frame of reference)."""
    found = []
    source = f"perception:{report.backend}"
    for at, count in report.counts:
        found.append(Observation(report.source, "in_view", count, observed_at=started + timedelta(seconds=at),
                                 source=source, sensitivity=sensitivity))
    for t in report.tracks:
        entity = f"{report.source} #{t.track_id}"
        for at, detection in t.points:
            x, y = detection.center
            found.append(Observation(entity, "position", {"x": round(x, 4), "y": round(y, 4), "frame": "image"},
                                     observed_at=started + timedelta(seconds=at), source=source, sensitivity=sensitivity))
        if t.label:
            found.append(Observation(entity, "label", t.label, observed_at=started + timedelta(seconds=t.first_at),
                                     source=source, sensitivity=sensitivity))
    found.append(Observation(report.source, "distinct_seen", len(report.tracks),
                             observed_at=started + timedelta(seconds=report.counts[-1][0] if report.counts else 0),
                             source=source, sensitivity=sensitivity))
    return found


def frames_from_media(data: bytes, media_type: str, *, count: int = 24) -> "list[Frame]":
    """Frames from a still or a moving picture - a moving one sampled evenly
    across its own duration with the existing FFmpeg primitive."""
    kind = (media_type or "").lower()
    if kind.startswith("image/"):
        return [Frame(0.0, data)]
    from src.providers.media.video import VideoError, sample_frames

    try:
        sampled = sample_frames(data, count=max(3, int(count)))
    except VideoError as exc:
        raise PerceptionError(str(exc)) from exc
    return [Frame(f.at_seconds, f.image_bytes) for f in sampled]


def utc_now() -> datetime:
    return datetime.now(timezone.utc)
