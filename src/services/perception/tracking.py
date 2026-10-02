"""Identity across frames (GM-03): which region in one frame is the same
thing as which region in the next.

A deterministic, greedy association that needs nothing but boxes: pairs are
scored by overlap (intersection over union) and, when two boxes do not
overlap, by how close their centres are relative to how far anything could
plausibly move between two frames; the best pairs are taken first, each region
and each track at most once. A region nobody continues starts a new track; a
track nobody continues for more than ``max_gap`` frames ends. Nothing is
assumed about what is being followed - the same code follows a vehicle, an
animal, a part on a conveyor or a shadow.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from typing import Sequence

from src.providers.perception.contract import Detection, FramePerception


@dataclass
class Track:
    track_id: int
    points: "list[tuple[float, Detection]]" = field(default_factory=list)
    label: str = ""
    missed: int = 0

    @property
    def first_at(self) -> float:
        return self.points[0][0]

    @property
    def last_at(self) -> float:
        return self.points[-1][0]

    @property
    def last(self) -> Detection:
        return self.points[-1][1]

    @property
    def centres(self) -> "list[tuple[float, float]]":
        return [d.center for _t, d in self.points]

    @property
    def travelled(self) -> float:
        """Distance along the path, in picture widths/heights."""
        c = self.centres
        return sum(math.dist(a, b) for a, b in zip(c, c[1:]))

    @property
    def displacement(self) -> "tuple[float, float]":
        (x0, y0), (x1, y1) = self.centres[0], self.centres[-1]
        return (x1 - x0, y1 - y0)

    @property
    def moved(self) -> bool:
        return math.hypot(*self.displacement) > 0.05 or self.travelled > 0.12

    def still_spans(self, *, still_frames: int = 3, eps: float = 0.02) -> "list[tuple[float, float]]":
        """(from, to) times of every stretch it stood still for at least
        ``still_frames`` frames - wherever along its path that happened."""
        # A moment where its region suddenly grew or shrank (something passed
        # in front of it, or merged with it) says nothing about whether *it*
        # moved: such moments neither start nor break a stretch of stillness.
        areas = sorted(d.area for _t, d in self.points)
        typical = areas[len(areas) // 2] if areas else 0.0
        points = [p for p in self.points if typical <= 0 or 0.6 <= p[1].area / typical <= 1.6] or list(self.points)
        spans = []
        start = 0
        for index in range(1, len(points) + 1):
            ended = index == len(points) or math.dist(points[index][1].center, points[start][1].center) > eps
            if ended:
                if index - start >= still_frames:
                    spans.append((points[start][0], points[index - 1][0]))
                start = index
        return spans

    def heading(self) -> str:
        """Where it went, in the picture's own terms."""
        dx, dy = self.displacement
        if math.hypot(dx, dy) <= 0.05:
            return "moved about and ended near where it began" if self.travelled > 0.12 else "stayed in place"
        parts = []
        if abs(dx) > 0.03:
            parts.append("left to right" if dx > 0 else "right to left")
        if abs(dy) > 0.03:
            parts.append("downwards" if dy > 0 else "upwards")
        return " and ".join(parts)

    def best(self) -> "tuple[float, Detection]":
        """Its clearest view: the largest, most confident box."""
        return max(self.points, key=lambda p: (p[1].area * (0.5 + p[1].confidence)))

    def stopped_at(self, still_frames: int = 3) -> "float | None":
        """When it came to rest, if it moved first and then stayed put for
        ``still_frames`` frames."""
        if len(self.points) < still_frames + 2 or not self.moved:
            return None
        tail = self.centres[-still_frames:]
        if max(math.dist(tail[0], c) for c in tail) > 0.02:
            return None
        return self.points[-still_frames][0]


def _iou(a: Detection, b: Detection) -> float:
    ax0, ay0, ax1, ay1 = a.box
    bx0, by0, bx1, by1 = b.box
    ix0, iy0, ix1, iy1 = max(ax0, bx0), max(ay0, by0), min(ax1, bx1), min(ay1, by1)
    inter = max(0.0, ix1 - ix0) * max(0.0, iy1 - iy0)
    union = a.area + b.area - inter
    return inter / union if union > 0 else 0.0


def _predicted(track: "Track", at: float) -> "tuple[float, float]":
    """Where the track should be at ``at``, from its own recent velocity -
    a thing moving steadily is looked for where it is going, not where it was."""
    (t1, d1) = track.points[-1]
    if len(track.points) < 2 or at <= t1:
        return d1.center
    (t0, d0) = track.points[-2]
    if t1 <= t0:
        return d1.center
    vx = (d1.center[0] - d0.center[0]) / (t1 - t0)
    vy = (d1.center[1] - d0.center[1]) / (t1 - t0)
    return (d1.center[0] + vx * (at - t1), d1.center[1] + vy * (at - t1))


def _appearance(track: "Track") -> Detection:
    """The track's recent look: its last detection, carrying the mean
    signature of its last few (one odd frame does not change what it is)."""
    recent = [d.signature for _t, d in track.points[-4:] if d.signature]
    last = track.last
    if not recent or len({len(s) for s in recent}) != 1:
        return last
    mean = tuple(sum(values) / len(recent) for values in zip(*recent))
    return Detection(box=last.box, confidence=last.confidence, label=last.label, signature=mean)


def associate(tracks: "list[Track]", detections: Sequence[Detection], *, max_distance: float,
              at: "float | None" = None) -> "list[tuple[int, int]]":
    """(track index, detection index) pairs, best first, each used once.
    Distance is measured from where each track was predicted to be, and the
    reach grows with the thing's own size - a large thing can move further
    between two frames than a small one and still be the same thing. Where
    two candidates are equally placed, the one that *looks* like the track
    (its colour) wins - so two things crossing keep who they are."""
    candidates = []
    for ti, track in enumerate(tracks):
        last = track.last
        look = _appearance(track)
        expected = _predicted(track, at) if at is not None else last.center
        x0, y0, x1, y1 = last.box
        size = math.hypot(x1 - x0, y1 - y0)
        for di, detection in enumerate(detections):
            overlap = _iou(last, detection)
            distance = min(math.dist(expected, detection.center), math.dist(last.center, detection.center))
            reach = max(max_distance, size * 1.2) * (1 + track.missed)
            if overlap > 0.05:
                score = 1.0 + overlap
            elif distance < reach:
                score = 1.0 - distance / reach
            else:
                continue
            likeness = look.looks_like(detection)
            if likeness < 0.2 and overlap <= 0.05:
                continue  # nearby, but plainly something else
            candidates.append((score * (0.35 + 0.65 * likeness), ti, di))
    candidates.sort(key=lambda c: (-c[0], c[1], c[2]))
    used_t, used_d, pairs = set(), set(), []
    for _score, ti, di in candidates:
        if ti in used_t or di in used_d:
            continue
        used_t.add(ti)
        used_d.add(di)
        pairs.append((ti, di))
    return pairs


def _velocity(points: "list[tuple[float, Detection]]") -> "tuple[float, float] | None":
    """Mean velocity (picture widths/heights per second) over ``points``."""
    if len(points) < 2 or points[-1][0] <= points[0][0]:
        return None
    (t0, d0), (t1, d1) = points[0], points[-1]
    return ((d1.center[0] - d0.center[0]) / (t1 - t0), (d1.center[1] - d0.center[1]) / (t1 - t0))


def _continues(a: "Track", b: "Track", *, tolerance: float) -> bool:
    """Whether ``b`` is ``a`` seen again: it starts after ``a`` ends, where
    ``a``'s own motion would have carried it (within ``tolerance`` of the
    distance travelled, plus the thing's size), moving the same way, and
    looking the same. Deterministic and blind to what the thing is."""
    gap = b.first_at - a.last_at
    if gap <= 0:
        return False
    first_b = b.points[0][1]
    x0, y0, x1, y1 = a.last.box
    size = math.hypot(x1 - x0, y1 - y0)
    if len(a.points) >= 2:
        # Carry the earlier piece forward along its own motion.
        velocity = _velocity(a.points[-3:])
        start, target = a.last.center, first_b.center
        step = gap
    elif len(b.points) >= 2:
        # A single earlier sighting: carry the later piece back along its own.
        velocity = _velocity(b.points[:3])
        start, target = first_b.center, a.last.center
        step = -gap
    else:
        return False  # two single sightings prove nothing about each other
    if velocity is None:
        return False
    speed = math.hypot(*velocity)
    expected = (start[0] + velocity[0] * step, start[1] + velocity[1] * step)
    if math.dist(expected, target) > tolerance * speed * gap + size * 0.6:
        return False
    if len(a.points) >= 2 and len(b.points) >= 2:
        after = _velocity(b.points[:3])
        if after is not None and speed > 0 and math.hypot(*after) > 0:
            cos = (velocity[0] * after[0] + velocity[1] * after[1]) / (speed * math.hypot(*after))
            if cos < 0.5:
                return False
    area_ratio = max(a.last.area, first_b.area) / max(1e-9, min(a.last.area, first_b.area))
    return area_ratio <= 3.0 and _appearance(a).looks_like(first_b) >= 0.5


def _seed_chains(pieces: "list[Track]", *, max_gap_seconds: float, tolerance: float) -> "list[Track]":
    """Three single sightings, in order, each where steady motion from the
    one before would put it and all looking alike, are one moving thing -
    how a thing too fast to be matched frame to frame is still followed."""
    singles = sorted((t for t in pieces if len(t.points) == 1), key=lambda t: t.first_at)
    used: set[int] = set()
    for i, s1 in enumerate(singles):
        if id(s1) in used:
            continue
        for s2 in singles[i + 1:]:
            if id(s2) in used or not (0 < s2.first_at - s1.first_at <= max_gap_seconds):
                continue
            d1, d2 = s1.last, s2.last
            if d1.looks_like(d2) < 0.5:
                continue
            dt1 = s2.first_at - s1.first_at
            v = ((d2.center[0] - d1.center[0]) / dt1, (d2.center[1] - d1.center[1]) / dt1)
            third = None
            for s3 in singles:
                if id(s3) in used or s3 is s1 or s3 is s2 or not (0 < s3.first_at - s2.first_at <= max_gap_seconds):
                    continue
                dt2 = s3.first_at - s2.first_at
                expected = (d2.center[0] + v[0] * dt2, d2.center[1] + v[1] * dt2)
                travelled = math.hypot(*v) * dt2
                if travelled > 0 and math.dist(expected, s3.last.center) <= tolerance * travelled and d2.looks_like(s3.last) >= 0.5:
                    third = s3
                    break
            if third is not None:
                s1.points.extend(s2.points + third.points)
                used.update({id(s2), id(third)})
                break
    return [t for t in pieces if id(t) not in used]


def link_fragments(tracks: "list[Track]", *, max_gap_seconds: float, tolerance: float = 0.45) -> "list[Track]":
    """Join pieces of one thing the frame-to-frame association lost - a small
    thing moving further between two sampled moments than it is wide, or one
    hidden for a moment - wherever the later piece is exactly where the
    earlier one's own steady motion would have put it. Repeats until nothing
    more joins; ids are renumbered in order of first appearance."""
    pieces = _seed_chains(sorted(tracks, key=lambda t: (t.first_at, t.track_id)),
                          max_gap_seconds=max_gap_seconds, tolerance=tolerance)
    changed = True
    while changed:
        changed = False
        for i, a in enumerate(pieces):
            best = None
            for j, b in enumerate(pieces):
                if j == i or not (0 < b.first_at - a.last_at <= max_gap_seconds):
                    continue
                if _continues(a, b, tolerance=tolerance):
                    distance = b.first_at - a.last_at
                    if best is None or distance < best[0]:
                        best = (distance, j)
            if best is not None:
                b = pieces[best[1]]
                a.points.extend(b.points)
                if b.label and not a.label:
                    a.label = b.label
                del pieces[best[1]]
                changed = True
                break
    for number, piece in enumerate(sorted(pieces, key=lambda t: (t.first_at, t.track_id)), 1):
        piece.track_id = number
    return sorted(pieces, key=lambda t: (t.first_at, t.track_id))


def track(perceptions: Sequence[FramePerception], *, max_distance: float = 0.18, max_gap: int = 2) -> "list[Track]":
    """Every track through the sequence, in order of first appearance."""
    active: list[Track] = []
    finished: list[Track] = []
    next_id = 1
    for perception in perceptions:
        detections = list(perception.detections)
        pairs = associate(active, detections, max_distance=max_distance, at=perception.at_seconds)
        matched_t = {ti for ti, _ in pairs}
        matched_d = {di for _, di in pairs}
        for ti, di in pairs:
            active[ti].points.append((perception.at_seconds, detections[di]))
            active[ti].missed = 0
        still_active = []
        for ti, current in enumerate(active):
            if ti in matched_t:
                still_active.append(current)
                continue
            current.missed += 1
            (still_active if current.missed <= max_gap else finished).append(current)
        active = still_active
        for di, detection in enumerate(detections):
            if di in matched_d:
                continue
            active.append(Track(track_id=next_id, points=[(perception.at_seconds, detection)]))
            next_id += 1
    followed = sorted(finished + active, key=lambda t: (t.first_at, t.track_id))
    times = [p.at_seconds for p in perceptions]
    steps = sorted(b - a for a, b in zip(times, times[1:]) if b > a)
    if not steps:
        return followed
    typical = steps[len(steps) // 2]
    # Joining asks for the thing's own motion and look to agree, so it may
    # bridge a longer disappearance (passing behind or merging with another
    # thing) than frame-to-frame association does.
    return link_fragments(followed, max_gap_seconds=typical * (max_gap + 6))
