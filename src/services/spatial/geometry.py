"""Plane geometry the spatial model is built from (GM-05) - numpy only.

**A calibration maps one frame to another.** A camera looking at the ground
sees positions in its own picture ("image" frame: 0..1 of width and height);
four or more points whose place on the ground is known fix a *homography* -
the exact projective map from the picture to the ground plane, perspective
included - so a thing's position in the picture becomes metres on the ground.
Two points with a known distance fix a *similarity* (scale, rotation,
offset) for a frame already free of perspective (a top-down view, a device's
own map). A ground frame given an origin on the Earth and a bearing becomes
latitude and longitude (a local tangent plane: exact to centimetres over a
field, a yard or a building site - not meant for continents).

Every function is a plain transform on numbers; nothing knows what is being
positioned.
"""

from __future__ import annotations

import math
from typing import Sequence

EARTH_RADIUS_M = 6_378_137.0

Point = "tuple[float, float]"


def _np():
    import numpy as np

    return np


def fit_homography(image_points: "Sequence[Point]", world_points: "Sequence[Point]"):
    """The 3x3 projective map taking each image point to its world point,
    by the normalised direct linear transform (least squares for more than
    four points). Raises ValueError for fewer than four, or points that do
    not fix a plane (three or more in a line)."""
    np = _np()
    if len(image_points) != len(world_points) or len(image_points) < 4:
        raise ValueError("a ground-plane calibration needs at least four points whose place is known")

    def normalise(points):
        pts = np.asarray(points, dtype=float)
        centre = pts.mean(axis=0)
        spread = np.sqrt(((pts - centre) ** 2).sum(axis=1)).mean() or 1.0
        scale = math.sqrt(2) / spread
        transform = np.array([[scale, 0, -scale * centre[0]], [0, scale, -scale * centre[1]], [0, 0, 1]])
        homogeneous = np.column_stack([pts, np.ones(len(pts))]) @ transform.T
        return homogeneous, transform

    src, t_src = normalise(image_points)
    dst, t_dst = normalise(world_points)
    rows = []
    for (x, y, _), (u, v, _) in zip(src, dst):
        rows.append([-x, -y, -1, 0, 0, 0, u * x, u * y, u])
        rows.append([0, 0, 0, -x, -y, -1, v * x, v * y, v])
    _u, singular, vt = np.linalg.svd(np.asarray(rows))
    if singular[-2] < 1e-9 * singular[0]:
        raise ValueError("those points do not fix a plane - at least four, and no three in a line")
    h = vt[-1].reshape(3, 3)
    h = np.linalg.inv(t_dst) @ h @ t_src
    return h / h[2, 2]


def apply_homography(h, point: Point) -> Point:
    x, y = point
    u = h[0, 0] * x + h[0, 1] * y + h[0, 2]
    v = h[1, 0] * x + h[1, 1] * y + h[1, 2]
    w = h[2, 0] * x + h[2, 1] * y + h[2, 2]
    if abs(w) < 1e-12:
        raise ValueError("that point lies on the horizon of this calibration - it has no place on the ground")
    return (float(u / w), float(v / w))


def fit_similarity(image_points: "Sequence[Point]", world_points: "Sequence[Point]"):
    """Scale, rotation and offset from two or more corresponding points
    (Umeyama's least-squares method, without reflection) as a 3x3 matrix."""
    np = _np()
    a = np.asarray(image_points, dtype=float)
    b = np.asarray(world_points, dtype=float)
    if len(a) != len(b) or len(a) < 2:
        raise ValueError("a scale calibration needs at least two points whose place is known")
    ca, cb = a.mean(axis=0), b.mean(axis=0)
    da, db = a - ca, b - cb
    variance = (da ** 2).sum() / len(a)
    if variance == 0:
        raise ValueError("the calibration points must not all be the same point")
    covariance = db.T @ da / len(a)
    u, s, vt = np.linalg.svd(covariance)
    d = np.eye(2)
    if np.linalg.det(u) * np.linalg.det(vt) < 0:
        d[1, 1] = -1
    rotation = u @ d @ vt
    scale = float(np.trace(np.diag(s) @ d) / variance)
    offset = cb - scale * rotation @ ca
    m = np.eye(3)
    m[:2, :2] = scale * rotation
    m[:2, 2] = offset
    return m


def residual_metres(transform, image_points: "Sequence[Point]", world_points: "Sequence[Point]") -> float:
    """How far, at worst, the calibration puts a known point from where it
    really is - the calibration's own honesty about its accuracy."""
    return max(math.dist(apply_homography(transform, p), q) for p, q in zip(image_points, world_points))


# -- the Earth -----------------------------------------------------------------

def to_geo(point: Point, origin: "tuple[float, float]", bearing_degrees: float = 0.0) -> "tuple[float, float]":
    """A ground point (metres; +y along ``bearing_degrees`` from north, +x
    90 degrees clockwise of it) as (latitude, longitude)."""
    x, y = point
    theta = math.radians(bearing_degrees)
    east = x * math.cos(theta) + y * math.sin(theta)
    north = -x * math.sin(theta) + y * math.cos(theta)
    lat0, lon0 = origin
    lat = lat0 + math.degrees(north / EARTH_RADIUS_M)
    lon = lon0 + math.degrees(east / (EARTH_RADIUS_M * math.cos(math.radians(lat0))))
    return (lat, lon)


def from_geo(latlon: "tuple[float, float]", origin: "tuple[float, float]", bearing_degrees: float = 0.0) -> Point:
    lat, lon = latlon
    lat0, lon0 = origin
    north = math.radians(lat - lat0) * EARTH_RADIUS_M
    east = math.radians(lon - lon0) * EARTH_RADIUS_M * math.cos(math.radians(lat0))
    theta = math.radians(bearing_degrees)
    x = east * math.cos(theta) - north * math.sin(theta)
    y = east * math.sin(theta) + north * math.cos(theta)
    return (x, y)


def haversine_metres(a: "tuple[float, float]", b: "tuple[float, float]") -> float:
    lat1, lon1, lat2, lon2 = map(math.radians, (*a, *b))
    h = math.sin((lat2 - lat1) / 2) ** 2 + math.cos(lat1) * math.cos(lat2) * math.sin((lon2 - lon1) / 2) ** 2
    return 2 * EARTH_RADIUS_M * math.asin(min(1.0, math.sqrt(h)))


# -- shapes ----------------------------------------------------------------------

def polygon_contains(polygon: "Sequence[Point]", point: Point) -> bool:
    """Even-odd ray casting; a point on an edge counts as inside."""
    x, y = point
    inside = False
    n = len(polygon)
    for i in range(n):
        (x1, y1), (x2, y2) = polygon[i], polygon[(i + 1) % n]
        if min(y1, y2) <= y <= max(y1, y2) and min(x1, x2) <= x <= max(x1, x2):
            cross = (x2 - x1) * (y - y1) - (y2 - y1) * (x - x1)
            if abs(cross) < 1e-12:
                return True
        if (y1 > y) != (y2 > y):
            if x < (x2 - x1) * (y - y1) / (y2 - y1) + x1:
                inside = not inside
    return inside


def polygon_area(polygon: "Sequence[Point]") -> float:
    return abs(sum(x1 * y2 - x2 * y1 for (x1, y1), (x2, y2) in zip(polygon, list(polygon[1:]) + [polygon[0]]))) / 2


def path_length(points: "Sequence[Point]") -> float:
    return sum(math.dist(a, b) for a, b in zip(points, points[1:]))
