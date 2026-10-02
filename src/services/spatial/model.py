"""The spatial model (GM-05): calibrations, geo reference, zones, and what
they make of every position the ecosystem observes.

**Positions come from anywhere, through one door.** A perception track's
place in a picture (GM-03), a device reporting ``{"x","y"}`` in its own map,
a tracker reporting ``{"lat","lon"}`` - each arrives as an observation
through ``EcosystemState.observe``; ``SpatialModel.on_arrival`` listens there,
so a new source of positions needs nothing here.

**What it adds is itself observations.** A position from a calibrated
source is also kept as ``ground_position`` (metres on that site's ground)
and, when the site has a geo origin, ``geo_position``; a thing inside a zone
is kept as its ``zone``; and each zone keeps how many things are ``inside``
it. So "tell me when anything enters the loading zone" is an ordinary
condition (GM-02), "how far did it travel" is arithmetic over a history
(GM-01), and a dashboard shows a zone like any other reading (GM-06).

**Declared, never guessed.** A calibration is points whose real place the
owner (or an agent, with their words) stated; its worst residual is kept and
reported, so a measurement always says how far it can be trusted. A zone is
a polygon in a named frame. Nothing is assumed about what is positioned.
"""

from __future__ import annotations

import math
import threading
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Iterable, Mapping, Sequence

from src.services.declared_store import DeclaredDocument, slug
from src.services.observations.extract import position_of
from src.services.observations.store import Observation
from src.services.spatial.geometry import (
    apply_homography,
    fit_homography,
    fit_similarity,
    from_geo,
    haversine_metres,
    path_length,
    polygon_area,
    polygon_contains,
    residual_metres,
    to_geo,
)

CALIBRATION_KINDS = ("plane", "scale", "ground")
ZONE_FRAMES = ("image", "ground", "geo")
DEFAULT_STALE_SECONDS = 60.0


def source_of(entity: str) -> str:
    """The source a positioned entity belongs to: a perception track
    "<source> #3" belongs to "<source>"; anything else is its own source."""
    base, sep, number = entity.rpartition(" #")
    return base if sep and number.isdigit() else entity


@dataclass(frozen=True)
class Calibration:
    source: str
    kind: str
    image_points: "tuple[tuple[float, float], ...]" = ()
    world_points: "tuple[tuple[float, float], ...]" = ()
    geo_origin: "tuple[float, float] | None" = None
    bearing_degrees: float = 0.0
    units: str = "m"
    declared_by: str = ""
    declared_at: str = ""

    def __post_init__(self) -> None:
        if not " ".join(str(self.source or "").split()):
            raise ValueError("a calibration names the source whose positions it maps")
        if self.kind not in CALIBRATION_KINDS:
            raise ValueError(f"a calibration is {', '.join(CALIBRATION_KINDS)} - not '{self.kind}'")
        object.__setattr__(self, "image_points", tuple((float(x), float(y)) for x, y in self.image_points))
        object.__setattr__(self, "world_points", tuple((float(x), float(y)) for x, y in self.world_points))
        if self.geo_origin is not None:
            lat, lon = (float(v) for v in self.geo_origin)
            if not (-90 <= lat <= 90 and -180 <= lon <= 180):
                raise ValueError("a geo origin is a latitude (-90..90) and a longitude (-180..180)")
            object.__setattr__(self, "geo_origin", (lat, lon))
        if self.kind == "ground" and self.geo_origin is None:
            raise ValueError("a 'ground' calibration (positions already in metres) needs a geo origin to add anything")
        self.transform()  # refuse a calibration that fixes nothing, at declaration

    @property
    def key(self) -> str:
        return slug(self.source)

    def transform(self):
        import numpy as np

        if self.kind == "plane":
            return fit_homography(self.image_points, self.world_points)
        if self.kind == "scale":
            return fit_similarity(self.image_points, self.world_points)
        return np.eye(3)

    def residual(self) -> float:
        if self.kind == "ground" or not self.image_points:
            return 0.0
        return residual_metres(self.transform(), self.image_points, self.world_points)

    def to_ground(self, point: "tuple[float, float]") -> "tuple[float, float]":
        return apply_homography(self.transform(), point)

    def describe(self) -> str:
        how = {"plane": f"{len(self.image_points)} ground points (perspective)",
               "scale": f"{len(self.image_points)} points (scale and rotation)",
               "ground": "positions already in metres"}[self.kind]
        geo = (f", anchored at {self.geo_origin[0]:.6f}, {self.geo_origin[1]:.6f} bearing {self.bearing_degrees:g}"
               if self.geo_origin else "")
        return f"{self.source}: {how}, worst residual {self.residual():.3g} {self.units}{geo}"

    def to_dict(self) -> dict:
        return {"source": self.source, "kind": self.kind, "image_points": [list(p) for p in self.image_points],
                "world_points": [list(p) for p in self.world_points],
                "geo_origin": list(self.geo_origin) if self.geo_origin else None,
                "bearing_degrees": self.bearing_degrees, "units": self.units,
                "declared_by": self.declared_by, "declared_at": self.declared_at}

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> "Calibration":
        return cls(source=str(data["source"]), kind=str(data["kind"]),
                   image_points=tuple(tuple(p) for p in data.get("image_points") or ()),
                   world_points=tuple(tuple(p) for p in data.get("world_points") or ()),
                   geo_origin=tuple(data["geo_origin"]) if data.get("geo_origin") else None,
                   bearing_degrees=float(data.get("bearing_degrees") or 0.0), units=str(data.get("units") or "m"),
                   declared_by=str(data.get("declared_by") or ""), declared_at=str(data.get("declared_at") or ""))


@dataclass(frozen=True)
class Zone:
    name: str
    frame: str
    polygon: "tuple[tuple[float, float], ...]"
    source: str = ""
    declared_by: str = ""
    declared_at: str = ""

    def __post_init__(self) -> None:
        name = " ".join(str(self.name or "").split())
        if not name:
            raise ValueError("a zone needs a name")
        if self.frame not in ZONE_FRAMES:
            raise ValueError(f"a zone is drawn in the {', '.join(ZONE_FRAMES)} frame - not '{self.frame}'")
        polygon = tuple((float(x), float(y)) for x, y in self.polygon)
        if len(polygon) < 3 or polygon_area(polygon) <= 0:
            raise ValueError("a zone needs at least three corners enclosing some area")
        object.__setattr__(self, "name", name)
        object.__setattr__(self, "polygon", polygon)

    @property
    def key(self) -> str:
        return slug(self.name)

    def applies_to(self, entity: str) -> bool:
        return not self.source or slug(source_of(entity)) == slug(self.source)

    def area(self) -> "float | None":
        return polygon_area(self.polygon) if self.frame == "ground" else None

    def describe(self) -> str:
        where = f" of {self.source}" if self.source else ""
        size = f", {self.area():.4g} square metres" if self.area() else ""
        return f"{self.name}: {len(self.polygon)} corners in the {self.frame} frame{where}{size}"

    def to_dict(self) -> dict:
        return {"name": self.name, "frame": self.frame, "polygon": [list(p) for p in self.polygon],
                "source": self.source, "declared_by": self.declared_by, "declared_at": self.declared_at}

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> "Zone":
        return cls(name=str(data["name"]), frame=str(data["frame"]), polygon=tuple(tuple(p) for p in data["polygon"]),
                   source=str(data.get("source") or ""), declared_by=str(data.get("declared_by") or ""),
                   declared_at=str(data.get("declared_at") or ""))


class _Registry:
    def __init__(self, path: Path, key: str, factory) -> None:
        self._document = DeclaredDocument(path, key=key)
        self._factory = factory

    def all(self) -> list:
        found = []
        for item in self._document.load():
            try:
                found.append(self._factory.from_dict(item))
            except (KeyError, ValueError, TypeError):
                continue
        return found

    def declare(self, item) -> Any:
        def change(items: list):
            items[:] = [i for i in items if slug(i.get(self._name_field(), "")) != item.key]
            items.append(item.to_dict())
            return item
        return self._document.update(change)

    def remove(self, name: str) -> bool:
        wanted = slug(name)

        def change(items: list) -> bool:
            before = len(items)
            items[:] = [i for i in items if slug(i.get(self._name_field(), "")) != wanted]
            return len(items) < before
        return self._document.update(change)

    def _name_field(self) -> str:
        return "source" if self._factory is Calibration else "name"


class SpatialModel:
    """Calibrations and zones under the ecosystem root, and the listener
    that turns every arriving position into what they imply."""

    def __init__(self, root: "Path | str", *, stale_seconds: float = DEFAULT_STALE_SECONDS) -> None:
        root = Path(root)
        self.calibrations = _Registry(root / "calibrations.json", "calibrations", Calibration)
        self.zones = _Registry(root / "zones.json", "zones", Zone)
        self._stale = timedelta(seconds=stale_seconds)
        self._lock = threading.Lock()
        # zone key -> entity -> (last seen at, inside); entity -> last zone
        self._members: "dict[str, dict[str, tuple[datetime, bool]]]" = {}
        self._last_zone: "dict[str, str]" = {}

    def calibration_for(self, entity: str) -> "Calibration | None":
        wanted = {slug(entity), slug(source_of(entity))}
        return next((c for c in self.calibrations.all() if c.key in wanted), None)

    def zone(self, name: str) -> "Zone | None":
        wanted = slug(name)
        return next((z for z in self.zones.all() if z.key == wanted), None)

    # -- turning one position into what it implies --------------------------

    def places(self, entity: str, value: Any) -> dict:
        """Every frame ``value`` can be placed in: {"image"|"planar"|"ground"|"geo": (a, b)}."""
        point = position_of(value)
        if point is None:
            return {}
        found: dict = {}
        calibration = self.calibration_for(entity)
        if "lat" in point:
            found["geo"] = (point["lat"], point["lon"])
            if calibration is not None and calibration.geo_origin is not None:
                found["ground"] = from_geo(found["geo"], calibration.geo_origin, calibration.bearing_degrees)
            return found
        raw = (point["x"], point["y"])
        found["image" if point.get("frame") == "image" else "planar"] = raw
        if calibration is not None:
            try:
                ground = raw if calibration.kind == "ground" else calibration.to_ground(raw)
            except ValueError:
                return found
            found["ground"] = ground
            if calibration.geo_origin is not None:
                found["geo"] = to_geo(ground, calibration.geo_origin, calibration.bearing_degrees)
        return found

    def _inside(self, zone: Zone, places: Mapping[str, Any]) -> "bool | None":
        point = places.get(zone.frame) or (places.get("planar") if zone.frame == "image" else None)
        if point is None:
            return None
        return polygon_contains(zone.polygon, point)

    def on_arrival(self, observations: Sequence[Observation], record) -> int:
        """Listener body: ``record`` is the ecosystem's own ``observe``."""
        positioned = [o for o in observations if (o.attribute == "position" or o.attribute.endswith(".position"))
                      and position_of(o.value) is not None]
        if not positioned:
            return 0
        zones = self.zones.all()
        derived: list[Observation] = []
        for observation in sorted(positioned, key=lambda o: o.observed_at):
            entity, at = observation.entity, observation.observed_at
            places = self.places(entity, observation.value)
            common = {"observed_at": at, "sensitivity": observation.sensitivity}
            if "ground" in places and self.calibration_for(entity) is not None:
                x, y = places["ground"]
                derived.append(Observation(entity, "ground_position", {"x": round(x, 4), "y": round(y, 4)},
                                           unit="m", source=f"spatial:{source_of(entity)}", **common))
                if "geo" in places and "lat" not in (position_of(observation.value) or {}):
                    lat, lon = places["geo"]
                    derived.append(Observation(entity, "geo_position", {"lat": round(lat, 7), "lon": round(lon, 7)},
                                               source=f"spatial:{source_of(entity)}", **common))
            now_in = ""
            touched: list[Zone] = []
            for zone in zones:
                if not zone.applies_to(entity):
                    continue
                inside = self._inside(zone, places)
                if inside is None:
                    continue
                touched.append(zone)
                with self._lock:
                    self._members.setdefault(zone.key, {})[entity] = (at, inside)
                if inside and not now_in:
                    now_in = zone.name
            with self._lock:
                changed = self._last_zone.get(entity) != now_in
                self._last_zone[entity] = now_in
            if changed and touched:
                derived.append(Observation(entity, "zone", now_in or "none", source="spatial:zones", **common))
            for zone in touched:
                derived.append(Observation(zone.name, "inside", self.occupancy(zone, at), source="spatial:zones",
                                           **common))
        return record(derived) if derived else 0

    def occupancy(self, zone: Zone, at: datetime) -> int:
        with self._lock:
            members = dict(self._members.get(zone.key, {}))
        return sum(1 for seen, inside in members.values() if inside and at - seen <= self._stale and seen <= at)

    def inside_now(self, zone: Zone, at: "datetime | None" = None) -> "list[str]":
        at = at or datetime.now(timezone.utc)
        with self._lock:
            members = dict(self._members.get(zone.key, {}))
        return sorted(e for e, (seen, inside) in members.items() if inside and at - seen <= self._stale)


# -- measurement over what was observed ---------------------------------------------

def track_measure(points: "Sequence[tuple[datetime, tuple[float, float]]]", *, geo: bool = False) -> dict:
    """Distance travelled, straight-line displacement, duration and speeds
    of one thing's path - in metres and seconds."""
    ordered = sorted(points, key=lambda p: p[0])
    if len(ordered) < 2:
        return {"points": len(ordered)}

    def step(a, b):
        return haversine_metres(a, b) if geo else math.dist(a, b)

    legs = [(step(a[1], b[1]), (b[0] - a[0]).total_seconds()) for a, b in zip(ordered, ordered[1:])]
    travelled = sum(d for d, _ in legs)
    duration = (ordered[-1][0] - ordered[0][0]).total_seconds()
    speeds = [d / s for d, s in legs if s > 0]
    return {
        "points": len(ordered), "travelled_m": travelled, "displacement_m": step(ordered[0][1], ordered[-1][1]),
        "duration_s": duration, "mean_speed_mps": travelled / duration if duration > 0 else None,
        "top_speed_mps": max(speeds) if speeds else None,
        "from": ordered[0][1], "to": ordered[-1][1],
    }
