"""Reconstructing 3D structure from captures, and measuring it (GM-05).

Three kinds of capture carry depth, and each becomes the same thing - a
calibrated grid of 3D points, a surface mesh over it, and measurements:

* **a depth image** (a depth camera, a phone's range sensor): each pixel is a
  distance along the camera's view; the camera's focal lengths and centre
  (or, stated as an assumption, a field of view) place it in 3D;
* **a height map** (a surface scanner, a profilometer, a terrain grid - as a
  grey image or a CSV of numbers): each cell is a height over a flat base at
  a known spacing;
* **a rectified stereo pair** (two cameras side by side): depth is found by
  matching blocks along each row (sum of absolute differences, sub-pixel
  refined) and triangulating with the focal length and baseline.

Structure-from-motion over ordinary photographs from arbitrary viewpoints is
*not* here: it needs feature matching and bundle adjustment this machine has
no engine for, and a reconstruction that invented depth would be worse than
none. Every result says what it assumed (a default field of view, units) and
how much of the capture it could use.

What is measured is geometric, never interpretive: the extent, the base
level, and every raised feature (a connected region standing above the base)
with its height, footprint and volume - a part on a board, a stone on a
path, a crate on a floor alike.
"""

from __future__ import annotations

import csv
import io
import math
from dataclasses import dataclass, field
from typing import Any, Mapping, Sequence

MAX_GRID = 180


class ReconstructionError(ValueError):
    """A plain reason a capture could not be reconstructed."""


@dataclass
class Reconstruction:
    kind: str
    points: Any                      # (rows, cols, 3) float array, NaN where nothing was measured
    units: str
    notes: "list[str]" = field(default_factory=list)
    measurements: "dict" = field(default_factory=dict)
    features: "list[dict]" = field(default_factory=list)
    coverage: float = 0.0

    def to_dict(self) -> dict:
        return {"kind": self.kind, "units": self.units, "notes": list(self.notes), "coverage": round(self.coverage, 4),
                "measurements": self.measurements, "features": self.features,
                "grid": list(self.points.shape[:2])}

    def describe(self) -> str:
        m = self.measurements
        u = self.units
        lines = [f"Reconstructed a {self.kind} into a {self.points.shape[1]}x{self.points.shape[0]} surface "
                 f"({self.coverage:.0%} of it measured)."]
        if m.get("extent"):
            ex = m["extent"]
            lines.append(f"Extent: {ex[0]:.4g} x {ex[1]:.4g} x {ex[2]:.4g} {u}.")
        if "nearest" in m:
            lines.append(f"Nearest surface {m['nearest']:.4g} {u}, farthest {m['farthest']:.4g} {u}.")
        if self.features:
            lines.append(f"{len(self.features)} raised feature(s) above the base level {m.get('base', 0):.4g} {u}:")
            for f in self.features[:12]:
                lines.append(f"  #{f['id']}: {f['height']:.4g} {u} high, footprint {f['footprint']:.4g} {u}^2, "
                             f"volume {f['volume']:.4g} {u}^3, centred at ({f['centre'][0]:.4g}, {f['centre'][1]:.4g}) {u}")
            if len(self.features) > 12:
                lines.append(f"  ... and {len(self.features) - 12} more")
        elif m.get("base") is not None:
            lines.append("Nothing stands out above the base level.")
        lines += [f"Assumed: {n}" for n in self.notes]
        return "\n".join(lines)


def _np():
    import numpy as np

    return np


def _grey(image_bytes: bytes):
    """A single-channel image as floats, keeping 16-bit depth where it exists."""
    from PIL import Image

    np = _np()
    try:
        image = Image.open(io.BytesIO(image_bytes))
        image.load()
    except Exception as exc:  # noqa: BLE001 - any unreadable capture is the same honest failure
        raise ReconstructionError(f"that capture is not an image this machine can read ({type(exc).__name__})") from exc
    if image.mode in ("I;16", "I;16B", "I;16L", "I", "F"):
        return np.asarray(image, dtype=np.float64), image.mode
    return np.asarray(image.convert("L"), dtype=np.float64), "L"


def _csv_grid(data: bytes):
    np = _np()
    try:
        rows = [[float(cell) if cell.strip() not in ("", "nan", "NaN") else math.nan for cell in row]
                for row in csv.reader(io.StringIO(data.decode("utf-8-sig"))) if row]
    except ValueError as exc:
        raise ReconstructionError("that table is not a grid of numbers") from exc
    width = max((len(r) for r in rows), default=0)
    if len(rows) < 3 or width < 3:
        raise ReconstructionError("a height grid needs at least 3 rows and 3 columns")
    return np.array([r + [math.nan] * (width - len(r)) for r in rows], dtype=np.float64)


def _shrink(grid, step: int):
    """Every ``step``-th cell - a nearest sample, so a measured edge is kept, never blurred."""
    return grid[::step, ::step] if step > 1 else grid


def _step_for(shape) -> int:
    return max(1, math.ceil(max(shape[0], shape[1]) / MAX_GRID))


# -- the three kinds of capture ----------------------------------------------------

def from_depth_image(data: bytes, *, fx: "float | None" = None, fy: "float | None" = None, cx: "float | None" = None,
                     cy: "float | None" = None, depth_scale: "float | None" = None, fov_degrees: float = 60.0,
                     units: str = "m") -> Reconstruction:
    np = _np()
    raw, mode = _grey(data)
    notes: list[str] = []
    if depth_scale is None:
        if mode == "L":
            raise ReconstructionError("an 8-bit picture carries no distance - give depth_scale (units per grey level)")
        depth_scale = 0.001
        notes.append("each depth unit is a millimetre (the usual depth-camera convention) - say depth_scale otherwise")
    height, width = raw.shape
    if fx is None:
        fx = width / (2 * math.tan(math.radians(fov_degrees) / 2))
        notes.append(f"a {fov_degrees:g} degree horizontal field of view (no focal length given)")
    fy = fy or fx
    cx = width / 2 if cx is None else cx
    cy = height / 2 if cy is None else cy
    z = raw * float(depth_scale)
    z[raw <= 0] = math.nan
    v, u = np.mgrid[0:height, 0:width].astype(np.float64)
    points = np.dstack([(u - cx) * z / fx, (v - cy) * z / fy, z])
    step = _step_for(points.shape)
    result = Reconstruction("depth image", _shrink(points, step), units, notes)
    return _measure_depth(result)


def from_height_map(data: bytes, *, is_table: bool = False, pitch: float = 1.0, height_scale: float = 1.0,
                    units: str = "mm") -> Reconstruction:
    np = _np()
    notes: list[str] = []
    if is_table:
        heights = _csv_grid(data) * float(height_scale)
    else:
        raw, mode = _grey(data)
        heights = raw * float(height_scale)
        if height_scale == 1.0:
            notes.append(f"each grey level is one {units} of height - say height_scale otherwise")
    if pitch == 1.0:
        notes.append(f"cells are 1 {units} apart - say pitch otherwise")
    rows, cols = heights.shape
    y, x = np.mgrid[0:rows, 0:cols].astype(np.float64) * float(pitch)
    points = np.dstack([x, y, heights])
    step = _step_for(points.shape)
    result = Reconstruction("height map", _shrink(points, step), units, notes)
    return _measure_heights(result, cell_area=(float(pitch) * step) ** 2)


def _box_sum(a, radius: int):
    np = _np()
    padded = np.pad(a, radius + 1, mode="edge")
    c = padded.cumsum(0).cumsum(1)
    size = 2 * radius + 1
    return (c[size:, size:] - c[:-size, size:] - c[size:, :-size] + c[:-size, :-size])[:a.shape[0], :a.shape[1]]


def _consistent(z, radius: int = 2, tolerance: float = 0.1):
    """Keep a depth only where it agrees with the median of its neighbours -
    a lone mismatch (a repeated texture matched one block too far) is
    dropped, never kept as a far-away point."""
    import warnings

    np = _np()
    padded = np.pad(z, radius, mode="constant", constant_values=np.nan)
    windows = np.lib.stride_tricks.sliding_window_view(padded, (2 * radius + 1, 2 * radius + 1))
    with warnings.catch_warnings():
        warnings.simplefilter("ignore", RuntimeWarning)
        local = np.nanmedian(windows.reshape(z.shape[0], z.shape[1], -1), axis=2)
    agree = np.abs(z - local) <= tolerance * np.abs(local)
    return np.where(agree, z, np.nan)


def from_stereo_pair(left: bytes, right: bytes, *, fx: float, baseline: float, max_disparity: int = 64,
                     block: int = 5, units: str = "m") -> Reconstruction:
    """Rectified pair: the same scene row lies on the same image row in both."""
    np = _np()
    a, _ = _grey(left)
    b, _ = _grey(right)
    if a.shape != b.shape:
        raise ReconstructionError("the two pictures of a stereo pair must be the same size")
    height, width = a.shape
    max_disparity = max(4, min(int(max_disparity), width // 2))
    radius = max(1, block // 2)
    costs = np.full((max_disparity, height, width), np.inf)
    for d in range(max_disparity):
        shifted = np.full_like(b, np.nan)
        shifted[:, d:] = b[:, :width - d] if d else b
        diff = np.abs(a - shifted)
        valid = ~np.isnan(diff)
        diff[~valid] = 0.0
        counted = _box_sum(valid.astype(float), radius)
        cost = _box_sum(diff, radius) / np.maximum(counted, 1)
        cost[counted < (2 * radius + 1) ** 2 * 0.6] = np.inf
        costs[d] = cost
    best = np.argmin(costs, axis=0)
    best_cost = np.take_along_axis(costs, best[None], axis=0)[0]
    second = np.partition(np.where(np.isfinite(costs), costs, 1e18), 1, axis=0)[1]
    texture = _box_sum(np.abs(np.gradient(a, axis=1)), radius) / (2 * radius + 1) ** 2
    # Keep a match only where it is clearly the best and the block has texture to match on.
    confident = np.isfinite(best_cost) & (best_cost < 0.85 * second) & (texture > 2.0) & (best > 0)
    # ... and where matching the other way round agrees: the right picture's
    # own best match for that pixel points back here. A left-border pixel whose
    # true match lies outside the right picture fails this and is dropped.
    costs_right = np.full_like(costs, np.inf)
    for shift in range(max_disparity):
        costs_right[shift][:, :width - shift] = costs[shift][:, shift:]
    best_right = np.argmin(costs_right, axis=0)
    columns = np.arange(width)[None, :] - best
    inside = columns >= 0
    back = np.take_along_axis(best_right, np.clip(columns, 0, width - 1), axis=1)
    confident &= inside & (np.abs(back - best) <= 1)
    # Sub-pixel: a parabola through the costs either side of the best.
    d = best.astype(float)
    inner = (best > 0) & (best < max_disparity - 1)
    idx = np.clip(best, 1, max_disparity - 2)
    c0 = np.take_along_axis(costs, (idx - 1)[None], axis=0)[0]
    c1 = np.take_along_axis(costs, idx[None], axis=0)[0]
    c2 = np.take_along_axis(costs, (idx + 1)[None], axis=0)[0]
    with np.errstate(invalid="ignore"):
        denom = c0 - 2 * c1 + c2
        good = inner & np.isfinite(denom) & (denom > 1e-9)
    d[good] = idx[good] + 0.5 * (c0[good] - c2[good]) / denom[good]
    z = np.where(confident & (d > 0.5), float(fx) * float(baseline) / np.maximum(d, 1e-6), np.nan)
    z = _consistent(z)
    v, u = np.mgrid[0:height, 0:width].astype(np.float64)
    points = np.dstack([(u - width / 2) * z / fx, (v - height / 2) * z / fx, z])
    step = _step_for(points.shape)
    notes = ["the pair is rectified (rows correspond) with the principal point at the picture's centre"]
    result = Reconstruction("stereo pair", _shrink(points, step), units, notes)
    return _measure_depth(result)


# -- measurement ------------------------------------------------------------------

def _extent(points) -> "list[float]":
    np = _np()
    flat = points.reshape(-1, 3)
    flat = flat[~np.isnan(flat).any(axis=1)]
    if not len(flat):
        return [0.0, 0.0, 0.0]
    return [float(v) for v in flat.max(axis=0) - flat.min(axis=0)]


def _measure_depth(result: Reconstruction) -> Reconstruction:
    np = _np()
    z = result.points[:, :, 2]
    measured = ~np.isnan(z)
    result.coverage = float(measured.mean())
    if not measured.any():
        raise ReconstructionError("nothing in that capture could be measured - no depth anywhere")
    result.measurements = {"extent": _extent(result.points), "nearest": float(np.nanmin(z)),
                           "farthest": float(np.nanmax(z)), "points": int(measured.sum())}
    # Things standing out towards the camera from the dominant far surface.
    base = float(np.nanpercentile(z, 90))
    rise = np.where(measured, base - z, np.nan)
    _features(result, rise, cell_area=None, base=base)
    return result


def _measure_heights(result: Reconstruction, *, cell_area: float) -> Reconstruction:
    np = _np()
    h = result.points[:, :, 2]
    measured = ~np.isnan(h)
    result.coverage = float(measured.mean())
    if not measured.any():
        raise ReconstructionError("that height map holds no heights")
    base = float(np.nanpercentile(h, 20))
    result.measurements = {"extent": _extent(result.points), "base": base, "highest": float(np.nanmax(h)),
                           "points": int(measured.sum())}
    _features(result, np.where(measured, h - base, np.nan), cell_area=cell_area, base=base)
    return result


def _features(result: Reconstruction, rise, *, cell_area: "float | None", base: float) -> None:
    """Connected regions standing clearly above the base (above the base's
    own roughness), each measured."""
    np = _np()
    from src.providers.perception.contract import components

    values = rise[~np.isnan(rise)]
    if not len(values):
        return
    roughness = float(np.median(np.abs(values - np.median(values)))) * 1.4826
    span = float(np.nanmax(rise)) if len(values) else 0.0
    threshold = max(4 * roughness, 0.08 * span, 1e-9)
    mask = np.nan_to_num(rise, nan=-1.0) > threshold
    result.measurements["base"] = base
    features = []
    xs, ys = result.points[:, :, 0], result.points[:, :, 1]
    # The ground each cell covers where it is: a depth camera's cells cover
    # more ground the farther away they are, so a crate's top is measured at
    # the crate's own distance, never the floor's.
    spacing_x = np.abs(np.diff(xs, axis=1, append=np.nan))
    spacing_y = np.abs(np.diff(ys, axis=0, append=np.nan))
    for x0, y0, x1, y1, count in components(mask, min_pixels=3):
        region = mask[y0:y1, x0:x1]
        heights = rise[y0:y1, x0:x1][region]
        px, py = xs[y0:y1, x0:x1][region], ys[y0:y1, x0:x1][region]
        if cell_area is None:
            dx = float(np.nanmedian(spacing_x[y0:y1, x0:x1][region]))
            dy = float(np.nanmedian(spacing_y[y0:y1, x0:x1][region]))
            area = max(dx * dy, 0.0) if math.isfinite(dx * dy) else 0.0
        else:
            area = cell_area
        features.append({
            "id": len(features) + 1, "height": float(np.nanmax(heights)), "footprint": float(count * area),
            "volume": float(np.nansum(heights) * area), "centre": [float(np.nanmean(px)), float(np.nanmean(py))],
            "cells": int(count),
        })
    features.sort(key=lambda f: -f["volume"])
    for number, feature in enumerate(features, 1):
        feature["id"] = number
    result.features = features


# -- a surface to look at -----------------------------------------------------------

def surface_mesh(result: Reconstruction, *, jump: float = 0.08):
    """(vertices, triangles) over the measured grid - a triangle only where
    all three corners were measured and do not straddle a depth jump - with
    glTF's Y axis up."""
    np = _np()
    grid = result.points
    rows, cols = grid.shape[:2]
    if result.kind == "height map":
        x, y, h = grid[:, :, 0], grid[:, :, 1], grid[:, :, 2]
        verts = np.dstack([x - np.nanmean(x), h, y - np.nanmean(y)])
        span = float(np.nanmax(h) - np.nanmin(h)) or 1.0
        depth = h
    else:
        verts = np.dstack([grid[:, :, 0], -grid[:, :, 1], -grid[:, :, 2]])
        span = float(np.nanmax(grid[:, :, 2]) - np.nanmin(grid[:, :, 2])) or 1.0
        depth = grid[:, :, 2]
    index = -np.ones((rows, cols), dtype=np.int64)
    valid = ~np.isnan(verts).any(axis=2)
    index[valid] = np.arange(int(valid.sum()))
    triangles = []
    limit = jump * span if result.kind == "height map" else None
    for r in range(rows - 1):
        for c in range(cols - 1):
            quad = [(r, c), (r, c + 1), (r + 1, c), (r + 1, c + 1)]
            if not all(valid[p] for p in quad):
                continue
            ds = [depth[p] for p in quad]
            if limit is None:
                near = min(ds)
                if max(ds) - near > 0.1 * max(near, 1e-9):
                    continue
            a, b, cc, d = (int(index[p]) for p in quad)
            triangles += [(a, cc, b), (b, cc, d)]
    if not triangles:
        raise ReconstructionError("no surface could be built - too little of the capture was measured")
    return verts[valid].astype("float32"), np.array(triangles, dtype="uint32")
