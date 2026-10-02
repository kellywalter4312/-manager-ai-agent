"""The governed boundary to the spatial model (GM-05): calibrations and geo
reference, zones, where a thing is, how far it went and how fast, how far
apart two things are, who is in a zone, and reconstructing a depth-bearing
capture into a measured 3D model.

Reading positions is capped by the holder's sensitivity ceiling like every
observation read. Declaring a calibration or a zone changes what later
positions mean, so it is a governed write (audited by the capability or the
Control Center that calls it). A reconstruction is a new model artifact
derived from the capture - the capture is never changed.
"""

from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

from src.agents.tools.tool import Tool, ToolSpec
from src.services.observations.extract import position_of
from src.services.spatial.model import Calibration, Zone, source_of, track_measure

SPATIAL_TOOL = "spatial"
_OPERATIONS = ("calibrations", "calibrate", "remove_calibration", "zones", "zone", "remove_zone", "where",
               "measure", "distance", "zone_state", "reconstruct")


def _points(raw) -> "tuple[tuple[float, float], ...]":
    return tuple((float(p[0]), float(p[1])) for p in raw or ())


class SpatialTool(Tool):
    def __init__(self, ecosystem: Any, artifacts: Any, *, owner_agent: str, max_sensitivity: str = "personal") -> None:
        self._ecosystem = ecosystem
        self._artifacts = artifacts
        self._owner_agent = owner_agent
        self._ceiling = max_sensitivity

    @property
    def spec(self) -> ToolSpec:
        return ToolSpec(
            name=SPATIAL_TOOL,
            description=("Where things are in real units: calibrations and geo reference, zones, how far something "
                         "went and how fast, distances, who is in a zone, and measured 3D models reconstructed from "
                         "depth images, height maps and stereo pairs."),
            schema={"operation": str},
            optional={"content": str},
        )

    @property
    def _model(self):
        return self._ecosystem.spatial

    def run(self, **arguments: str) -> str:
        operation = str(arguments.get("operation") or "").strip().lower()
        raw = str(arguments.get("content") or "").strip()
        content = json.loads(raw) if raw else {}
        if operation not in _OPERATIONS:
            raise ValueError(f"unknown spatial operation {operation!r}; expected one of {', '.join(_OPERATIONS)}")
        return json.dumps(getattr(self, f"_{operation}")(content), default=str)

    # -- declarations --------------------------------------------------------

    def _calibrations(self, content: dict) -> dict:
        return {"calibrations": [{**c.to_dict(), "text": c.describe(), "residual": c.residual()}
                                 for c in self._model.calibrations.all()]}

    def _calibrate(self, content: dict) -> dict:
        calibration = Calibration(
            source=str(content.get("source") or ""), kind=str(content.get("kind") or "plane"),
            image_points=_points(content.get("image_points")), world_points=_points(content.get("world_points")),
            geo_origin=tuple(content["geo_origin"]) if content.get("geo_origin") else None,
            bearing_degrees=float(content.get("bearing_degrees") or 0.0), units=str(content.get("units") or "m"),
            declared_by=str(content.get("declared_by") or self._owner_agent),
            declared_at=datetime.now(timezone.utc).isoformat(),
        )
        self._model.calibrations.declare(calibration)
        return {"calibration": calibration.to_dict(), "text": calibration.describe(), "residual": calibration.residual()}

    def _remove_calibration(self, content: dict) -> dict:
        return {"removed": self._model.calibrations.remove(str(content.get("source") or ""))}

    def _zones(self, content: dict) -> dict:
        return {"zones": [{**z.to_dict(), "text": z.describe()} for z in self._model.zones.all()]}

    def _zone(self, content: dict) -> dict:
        zone = Zone(name=str(content.get("name") or ""), frame=str(content.get("frame") or "ground"),
                    polygon=_points(content.get("polygon")), source=str(content.get("source") or ""),
                    declared_by=str(content.get("declared_by") or self._owner_agent),
                    declared_at=datetime.now(timezone.utc).isoformat())
        self._model.zones.declare(zone)
        return {"zone": zone.to_dict(), "text": zone.describe()}

    def _remove_zone(self, content: dict) -> dict:
        return {"removed": self._model.zones.remove(str(content.get("name") or ""))}

    # -- reading -----------------------------------------------------------

    def _series(self, entity: str, attribute: str, window: "float | None") -> list:
        store = self._ecosystem.observations
        resolved = store.resolve_entity(entity) or entity
        since = datetime.now(timezone.utc) - timedelta(seconds=window) if window else None
        return [o for o in store.series(resolved, attribute, since=since, limit=5000,
                                        max_sensitivity=self._ceiling) if o.attribute == attribute]

    def _where(self, content: dict) -> dict:
        entity = str(content.get("entity") or "")
        store = self._ecosystem.observations
        resolved = store.resolve_entity(entity) or entity
        latest = store.latest(resolved, max_sensitivity=self._ceiling)
        found = {name: {"value": o.value, "at": o.observed_at.isoformat()}
                 for name, o in latest.items()
                 if name in ("ground_position", "geo_position", "zone") or name == "position" or name.endswith(".position")}
        return {"entity": resolved, "positions": found,
                "calibrated": self._model.calibration_for(resolved) is not None}

    def _measure(self, content: dict) -> dict:
        entity = str(content.get("entity") or "")
        window = float(content.get("window_seconds") or 0) or None
        ground = self._series(entity, "ground_position", window)
        if len(ground) >= 2:
            points = [(o.observed_at, (o.value["x"], o.value["y"])) for o in ground]
            return {"entity": entity, "frame": "ground", "units": "m", **track_measure(points)}
        geo = [o for o in self._series(entity, "position", window) if (position_of(o.value) or {}).get("lat") is not None]
        if len(geo) >= 2:
            points = [(o.observed_at, (o.value["lat"], o.value["lon"])) for o in geo]
            return {"entity": entity, "frame": "geo", "units": "m", **track_measure(points, geo=True)}
        raw = [o for o in self._series(entity, "position", window) if position_of(o.value) is not None]
        if len(raw) >= 2:
            points = [(o.observed_at, (o.value["x"], o.value["y"])) for o in raw]
            frame = raw[-1].value.get("frame") or "its own"
            return {"entity": entity, "frame": frame, "units": "", "uncalibrated": True, **track_measure(points)}
        return {"entity": entity, "points": len(ground) or len(raw)}

    def _latest_point(self, entity: str) -> "tuple[str, tuple[float, float]] | None":
        where = self._where({"entity": entity})["positions"]
        if "ground_position" in where:
            v = where["ground_position"]["value"]
            return ("ground:" + source_of(entity), (v["x"], v["y"]))
        if "geo_position" in where:
            v = where["geo_position"]["value"]
            return ("geo", (v["lat"], v["lon"]))
        for name, row in where.items():
            point = position_of(row["value"])
            if point and "lat" in point:
                return ("geo", (point["lat"], point["lon"]))
        return None

    def _distance(self, content: dict) -> dict:
        from src.services.spatial.geometry import haversine_metres
        import math

        a, b = str(content.get("a") or ""), str(content.get("b") or "")
        pa, pb = self._latest_point(a), self._latest_point(b)
        if pa is None or pb is None:
            missing = a if pa is None else b
            return {"a": a, "b": b, "distance_m": None,
                    "why": f"'{missing}' has no calibrated or geographic position yet"}
        if pa[0] == "geo" and pb[0] == "geo":
            return {"a": a, "b": b, "distance_m": haversine_metres(pa[1], pb[1]), "frame": "geo"}
        if pa[0] != pb[0]:
            return {"a": a, "b": b, "distance_m": None,
                    "why": "they are positioned on two different sites with no geo origin to relate them"}
        return {"a": a, "b": b, "distance_m": math.dist(pa[1], pb[1]), "frame": pa[0]}

    def _zone_state(self, content: dict) -> dict:
        zone = self._model.zone(str(content.get("zone") or ""))
        if zone is None:
            raise ValueError(f"there is no zone called '{content.get('zone')}'")
        latest = self._ecosystem.observations.latest(zone.name, max_sensitivity=self._ceiling)
        count = latest["inside"].value if "inside" in latest else 0
        return {"zone": zone.to_dict(), "text": zone.describe(), "inside": count,
                "members": self._model.inside_now(zone),
                "at": latest["inside"].observed_at.isoformat() if "inside" in latest else None}

    # -- reconstruction ----------------------------------------------------------

    def _bytes(self, artifact_id: str) -> "tuple[Any, bytes, str]":
        artifact = self._artifacts.get(str(artifact_id).strip().lower())
        if artifact is None or artifact.owner_agent != self._owner_agent:
            raise ValueError(f"no artifact of mine found with id {artifact_id!r}")
        for fmt, path in dict(artifact.latest.renditions).items():
            if Path(str(path)).is_file():
                return artifact, Path(str(path)).read_bytes(), fmt
        raise ValueError(f"'{artifact.latest.title}' has no file to reconstruct from")

    def _reconstruct(self, content: dict) -> dict:
        from src.providers.media.model3d import surface_glb
        from src.services.artifacts.registration import register_new_artifact
        from src.services.spatial.reconstruct import (
            from_depth_image,
            from_height_map,
            from_stereo_pair,
            surface_mesh,
        )

        kind = str(content.get("kind") or "depth").lower()
        params = {k: v for k, v in dict(content.get("params") or {}).items() if v is not None}
        artifact, data, fmt = self._bytes(str(content.get("artifact_id") or ""))
        related = [artifact.artifact_id]
        if kind in ("depth", "depth image"):
            result = from_depth_image(data, **{k: float(v) for k, v in params.items() if k != "units"},
                                      units=str(params.get("units") or "m"))
        elif kind in ("height", "height map", "heights"):
            result = from_height_map(data, is_table=fmt in ("csv", "txt"),
                                     **{k: float(v) for k, v in params.items() if k != "units"},
                                     units=str(params.get("units") or "mm"))
        elif kind == "stereo":
            other, right, _fmt = self._bytes(str(content.get("other_artifact_id") or ""))
            related.append(other.artifact_id)
            result = from_stereo_pair(data, right, **{k: (int(v) if k in ("max_disparity", "block") else float(v))
                                                      for k, v in params.items() if k != "units"},
                                      units=str(params.get("units") or "m"))
        else:
            raise ValueError(f"'{kind}' is not a capture I can reconstruct - depth, height or stereo")
        vertices, triangles = surface_mesh(result)
        title = f"Reconstruction of {artifact.latest.title}"[:120]
        glb = surface_glb(vertices, triangles, title=title)
        model = register_new_artifact(
            self._artifacts, type="model3d", owner_agent=self._owner_agent, title=title,
            content_bytes=json.dumps({"reconstruction": result.to_dict(), "source": related}, indent=2).encode("utf-8"),
            rendition_format="glb", rendition_bytes=glb, related_artifact_ids=tuple(related),
            metadata={"content_kind": "model3d", "media_type": "model/gltf-binary", "generator": "reconstruction",
                      "reconstruction": result.kind},
        )
        return {**result.to_dict(), "text": result.describe(), "model_artifact_id": model.artifact_id,
                "vertices": int(len(vertices)), "triangles": int(len(triangles))}
