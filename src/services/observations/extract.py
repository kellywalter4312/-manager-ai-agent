"""Structured answers (D-1) -> observations, for any service, any shape.

A connection that answers ``{"chamber": {"temp_c": 871.5, "door": "closed"},
"fans": [{"id": "a", "rpm": 1200}, {"id": "b", "rpm": 1180}]}`` yields
``chamber.temp_c = 871.5``, ``chamber.door = "closed"``, ``fans.a.rpm = 1200``
and ``fans.b.rpm = 1180``. Nothing here knows what a chamber or a fan is: the
paths are the service's own words.

Three structural rules, no subject rules:

* **Leaves.** Numbers, booleans and short strings are observed; long text is
  not a reading and is skipped (it belongs to Memory or an artifact).
* **Positions stay whole.** An object that is exactly a position - ``lat``/
  ``lon`` (or ``latitude``/``longitude``), or ``x``/``y`` with an optional
  ``z`` - is observed as one structured value, so the spatial model (GM-05)
  can reason about it as a point rather than as unrelated numbers.
* **Named members.** In a list of objects that each carry an identifying
  field (``id``, ``name``, ``key``, ``label``), a member's path uses that
  identity instead of its position, so "fan b" stays fan b when the service
  reorders its list.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any, Iterable, Sequence

from src.services.observations.store import MAX_STRING_VALUE, Observation, attribute_key, is_number

MAX_LEAVES = 400
MAX_LIST_ITEMS = 64
_IDENTITY_FIELDS = ("id", "name", "key", "label")
_GEO_KEYS = ({"lat", "lon"}, {"lat", "lng"}, {"latitude", "longitude"})
_PLANAR_KEYS = ({"x", "y"}, {"x", "y", "z"})


def position_of(value: Any) -> "dict | None":
    """``value`` as a position, when it is one: ``{"lat":..,"lon":..[,"alt":..]}``
    or ``{"x":..,"y":..[,"z":..]}`` with numeric coordinates. None otherwise."""
    if not isinstance(value, dict):
        return None
    lowered = {str(k).lower(): v for k, v in value.items()}
    keys = set(lowered)
    core = keys - {"alt", "altitude", "elevation", "frame", "accuracy"}
    for geo in _GEO_KEYS:
        if core == geo and all(is_number(lowered[k]) for k in geo):
            lat_key = "lat" if "lat" in geo else "latitude"
            lon_key = next(k for k in geo if k != lat_key)
            point = {"lat": float(lowered[lat_key]), "lon": float(lowered[lon_key])}
            for alt in ("alt", "altitude", "elevation"):
                if is_number(lowered.get(alt)):
                    point["alt"] = float(lowered[alt])
            if isinstance(lowered.get("frame"), str):
                point["frame"] = lowered["frame"]
            return point
    for planar in _PLANAR_KEYS:
        if core == planar and all(is_number(lowered[k]) for k in planar):
            point = {axis: float(lowered[axis]) for axis in sorted(planar)}
            if isinstance(lowered.get("frame"), str):
                point["frame"] = lowered["frame"]
            return point
    return None


def _identity(item: Any) -> "str | None":
    if not isinstance(item, dict):
        return None
    for field in _IDENTITY_FIELDS:
        value = item.get(field)
        if isinstance(value, (str, int)) and not isinstance(value, bool) and str(value).strip():
            return str(value).strip()
    return None


def flatten(data: Any, *, prefix: str = "", limit: int = MAX_LEAVES) -> "list[tuple[str, Any]]":
    """Every observable leaf of ``data`` as ``(attribute path, value)``."""
    leaves: list[tuple[str, Any]] = []

    def walk(value: Any, path: str) -> None:
        if len(leaves) >= limit:
            return
        position = position_of(value)
        if position is not None:
            leaves.append((path or "position", position))
            return
        if isinstance(value, bool) or is_number(value):
            leaves.append((path or "value", value))
            return
        if isinstance(value, str):
            text = " ".join(value.split())
            if text and len(text) <= MAX_STRING_VALUE:
                leaves.append((path or "value", text))
            return
        if isinstance(value, dict):
            for key in value:
                if key in _IDENTITY_FIELDS and path:
                    continue  # the member's identity is already its path
                walk(value[key], f"{path}.{key}" if path else str(key))
            return
        if isinstance(value, (list, tuple)):
            identities = [_identity(item) for item in value[:MAX_LIST_ITEMS]]
            named = all(identities) and len(set(identities)) == len(identities)
            for index, item in enumerate(value[:MAX_LIST_ITEMS]):
                segment = identities[index] if named else str(index)
                walk(item, f"{path}.{segment}" if path else segment)

    walk(data, attribute_key(prefix) if prefix else "")
    return [(attribute_key(path), value) for path, value in leaves if attribute_key(path)]


def select(leaves: Sequence[tuple[str, Any]], keep: Iterable[str]) -> "list[tuple[str, Any]]":
    """Only the leaves at, or under, the attribute paths named in ``keep``
    (all of them when ``keep`` is empty)."""
    wanted = [attribute_key(path) for path in keep if attribute_key(path)]
    if not wanted:
        return list(leaves)
    return [(path, value) for path, value in leaves if any(path == w or path.startswith(w + ".") for w in wanted)]


def observations_from(
    data: Any,
    *,
    entity: str,
    source: str,
    observed_at: datetime,
    keep: Iterable[str] = (),
    units: "dict[str, str] | None" = None,
    sensitivity: str = "",
) -> "list[Observation]":
    """The observations one structured answer yields."""
    units = {attribute_key(k): v for k, v in (units or {}).items()}
    observations = []
    for path, value in select(flatten(data), keep):
        try:
            observations.append(Observation(
                entity=entity, attribute=path, value=value, observed_at=observed_at,
                source=source, unit=units.get(path, ""), sensitivity=sensitivity,
            ))
        except ValueError:
            continue
    return observations
