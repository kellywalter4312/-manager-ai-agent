"""Genuine 3D content - a subject described as parts becomes a real glTF 2.0
binary (GLB) mesh, validated as 3D before it is ever called that.

    description or plan   "a grey box 4x1x2 for the hull, a grey cylinder turret
                          radius 0.8 height 0.6 on top of the hull, ..."
            |  parse_part_description / a Brain restating a subject in that grammar
            v
    PartPlan              named parts: a primitive shape, its dimensions, colour,
            |             and where it sits relative to the others
            v  assemble_glb
    GLB bytes             one node + mesh + material per part, real triangles
            |             with normals, a scene graph carrying the placement
            v  inspect_glb
    Model3DReport         read back from the bytes alone: structure valid,
                          triangle count, world bounds - and ``genuinely_3d``

**What "genuinely 3D" means here** (``inspect_glb``): the bytes are a valid glTF
2.0 GLB (header, JSON and BIN chunks, every accessor inside the buffer, every
index inside its vertex range); there is at least one triangle mesh with normals;
and the world bounding box has real extent on all three axes, the thinnest at
least ``MIN_DEPTH_RATIO`` of the widest. A flat picture fails on the last test;
an empty or malformed file fails on the first. Nothing is called 3D on a
filename or a claim.

**No subject is known here.** Shapes are geometry (box, sphere, cylinder, cone,
torus - an open registry); placement is geometry ("on top of", "beside",
"around", "at the corners of"). A lamp, a boat and a molecule are all just parts.
How faithful a model looks depends on how well its parts were described - by the
person, or by a reasoning model - never on a branch written for that subject.
"""

from __future__ import annotations

import json
import math
import re
import struct
from dataclasses import dataclass, field
from typing import Any, Callable, Mapping, Sequence

import numpy as np

MIN_DEPTH_RATIO = 0.02
MAX_PARTS = 200
MAX_EXTENT = 10_000.0

# ---------------------------------------------------------------------------
# Primitive shapes - an open registry of geometry, never of subjects
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class Shape:
    name: str
    aliases: "tuple[str, ...]"
    # dimensions -> (vertices Nx3, normals Nx3, triangles Mx3) centred on the origin
    mesh: "Callable[[Mapping[str, float]], tuple[np.ndarray, np.ndarray, np.ndarray]]" = field(repr=False)
    # dimensions -> local half extents (x, y, z)
    half_extents: "Callable[[Mapping[str, float]], tuple[float, float, float]]" = field(repr=False)
    defaults: "Mapping[str, float]" = field(default_factory=dict)


def _box(d):
    hx, hy, hz = d["width"] / 2, d["height"] / 2, d["depth"] / 2
    verts, norms, tris = [], [], []
    for axis in range(3):
        for sign in (1.0, -1.0):
            normal = [0.0, 0.0, 0.0]
            normal[axis] = sign
            u, v = [a for a in range(3) if a != axis]
            base = len(verts)
            for cu, cv in ((-1, -1), (1, -1), (1, 1), (-1, 1)):
                p = [0.0, 0.0, 0.0]
                p[axis] = sign * (hx, hy, hz)[axis]
                p[u] = cu * (hx, hy, hz)[u]
                p[v] = cv * (hx, hy, hz)[v]
                verts.append(p)
                norms.append(normal)
            a, b, c, e = base, base + 1, base + 2, base + 3
            # wind counter-clockwise when seen from outside
            if np.dot(np.cross(np.subtract(verts[b], verts[a]), np.subtract(verts[c], verts[a])), normal) > 0:
                tris += [(a, b, c), (a, c, e)]
            else:
                tris += [(a, c, b), (a, e, c)]
    return np.array(verts, np.float32), np.array(norms, np.float32), np.array(tris, np.uint32)


def _sphere(d, rings=16, segments=32):
    r = d["radius"]
    verts, norms, tris = [], [], []
    for i in range(rings + 1):
        theta = math.pi * i / rings
        for j in range(segments + 1):
            phi = 2 * math.pi * j / segments
            n = (math.sin(theta) * math.cos(phi), math.cos(theta), math.sin(theta) * math.sin(phi))
            verts.append([r * n[0], r * n[1], r * n[2]])
            norms.append(n)
    for i in range(rings):
        for j in range(segments):
            a, b = i * (segments + 1) + j, (i + 1) * (segments + 1) + j
            if i != 0:
                tris.append((a, a + 1, b))
            if i != rings - 1:
                tris.append((a + 1, b + 1, b))
    return np.array(verts, np.float32), np.array(norms, np.float32), np.array(tris, np.uint32)


def _lathe(profile, segments=32):
    """A solid of revolution around y from (radius, y) points, with flat caps."""
    verts, norms, tris = [], [], []
    for k in range(len(profile) - 1):
        (r0, y0), (r1, y1) = profile[k], profile[k + 1]
        slope = math.atan2(r0 - r1, y1 - y0) if (y1 - y0) or (r0 - r1) else 0.0
        base = len(verts)
        for j in range(segments + 1):
            phi = 2 * math.pi * j / segments
            c, s = math.cos(phi), math.sin(phi)
            n = (math.cos(slope) * c, math.sin(slope), math.cos(slope) * s)
            verts += [[r0 * c, y0, r0 * s], [r1 * c, y1, r1 * s]]
            norms += [n, n]
        for j in range(segments):
            a = base + 2 * j
            tris += [(a, a + 1, a + 3), (a, a + 3, a + 2)]
    for (r, y), up in ((profile[0], -1.0), (profile[-1], 1.0)):
        if r <= 0:
            continue
        centre = len(verts)
        verts.append([0.0, y, 0.0])
        norms.append((0.0, up, 0.0))
        for j in range(segments + 1):
            phi = 2 * math.pi * j / segments
            verts.append([r * math.cos(phi), y, r * math.sin(phi)])
            norms.append((0.0, up, 0.0))
        for j in range(segments):
            a, b = centre + 1 + j, centre + 2 + j
            tris.append((centre, b, a) if up > 0 else (centre, a, b))
    return np.array(verts, np.float32), np.array(norms, np.float32), np.array(tris, np.uint32)


def _cylinder(d):
    h = d["height"] / 2
    return _lathe([(d["radius"], -h), (d["radius"], h)])


def _cone(d):
    h = d["height"] / 2
    return _lathe([(d["radius"], -h), (0.0, h)])


def _torus(d, rings=32, segments=16):
    big, small = d["radius"], d["tube"]
    verts, norms, tris = [], [], []
    for i in range(rings + 1):
        u = 2 * math.pi * i / rings
        for j in range(segments + 1):
            v = 2 * math.pi * j / segments
            n = (math.cos(v) * math.cos(u), math.sin(v), math.cos(v) * math.sin(u))
            verts.append([(big + small * math.cos(v)) * math.cos(u), small * math.sin(v), (big + small * math.cos(v)) * math.sin(u)])
            norms.append(n)
    for i in range(rings):
        for j in range(segments):
            a, b = i * (segments + 1) + j, (i + 1) * (segments + 1) + j
            tris += [(a, b, a + 1), (b, b + 1, a + 1)]
    return np.array(verts, np.float32), np.array(norms, np.float32), np.array(tris, np.uint32)


SHAPES: "dict[str, Shape]" = {}


def register_shape(shape: Shape) -> None:
    """Add a primitive - described, meshed and placed like the built-in ones."""
    SHAPES[shape.name] = shape


for _shape in (
    Shape("box", ("box", "cube", "block", "brick", "slab", "plank", "board", "panel", "cuboid", "crate"), _box,
          lambda d: (d["width"] / 2, d["height"] / 2, d["depth"] / 2), {"width": 1.0, "height": 1.0, "depth": 1.0}),
    Shape("sphere", ("sphere", "ball", "globe", "orb", "dome", "ellipsoid"), _sphere,
          lambda d: (d["radius"],) * 3, {"radius": 0.5}),
    Shape("cylinder", ("cylinder", "tube", "pole", "rod", "disc", "disk", "pillar", "column", "shaft", "post"), _cylinder,
          lambda d: (d["radius"], d["height"] / 2, d["radius"]), {"radius": 0.5, "height": 1.0}),
    Shape("cone", ("cone", "spike", "point"), _cone, lambda d: (d["radius"], d["height"] / 2, d["radius"]), {"radius": 0.5, "height": 1.0}),
    Shape("torus", ("torus", "ring", "donut", "doughnut", "hoop"), _torus,
          lambda d: (d["radius"] + d["tube"], d["tube"], d["radius"] + d["tube"]), {"radius": 0.5, "tube": 0.15}),
):
    register_shape(_shape)


# ---------------------------------------------------------------------------
# Parts and their placement
# ---------------------------------------------------------------------------


@dataclass
class Part:
    name: str
    shape: str
    dimensions: "dict[str, float]"
    colour: "tuple[int, int, int]" = (200, 200, 200)
    position: "list[float]" = field(default_factory=lambda: [0.0, 0.0, 0.0])
    rotation: "list[float]" = field(default_factory=lambda: [0.0, 0.0, 0.0])  # degrees about x, y, z
    scale: "list[float]" = field(default_factory=lambda: [1.0, 1.0, 1.0])
    note: str = ""

    def to_dict(self) -> dict:
        return {"name": self.name, "shape": self.shape, "dimensions": dict(self.dimensions), "colour": list(self.colour),
                "position": [round(v, 4) for v in self.position], "rotation": list(self.rotation),
                "scale": list(self.scale), "note": self.note}


class Model3DError(ValueError):
    """A plain, person-facing reason a model could not be built."""


def _quaternion(rotation_degrees: Sequence[float]) -> "tuple[float, float, float, float]":
    ax, ay, az = (math.radians(v) / 2 for v in rotation_degrees)
    cx, sx, cy, sy, cz, sz = math.cos(ax), math.sin(ax), math.cos(ay), math.sin(ay), math.cos(az), math.sin(az)
    # intrinsic x, then y, then z  (q = qz * qy * qx)
    w = cz * cy * cx + sz * sy * sx
    x = cz * cy * sx - sz * sy * cx
    y = cz * sy * cx + sz * cy * sx
    z = sz * cy * cx - cz * sy * sx
    return (x, y, z, w)


def _matrix(part: Part) -> np.ndarray:
    x, y, z, w = _quaternion(part.rotation)
    rotation = np.array([
        [1 - 2 * (y * y + z * z), 2 * (x * y - z * w), 2 * (x * z + y * w)],
        [2 * (x * y + z * w), 1 - 2 * (x * x + z * z), 2 * (y * z - x * w)],
        [2 * (x * z - y * w), 2 * (y * z + x * w), 1 - 2 * (x * x + y * y)],
    ])
    m = np.eye(4)
    m[:3, :3] = rotation @ np.diag(part.scale)
    m[:3, 3] = part.position
    return m


def world_bounds(part: Part) -> "tuple[np.ndarray, np.ndarray]":
    shape = SHAPES[part.shape]
    hx, hy, hz = shape.half_extents(part.dimensions)
    corners = np.array([[sx * hx, sy * hy, sz * hz, 1.0] for sx in (-1, 1) for sy in (-1, 1) for sz in (-1, 1)])
    placed = (_matrix(part) @ corners.T).T[:, :3]
    return placed.min(axis=0), placed.max(axis=0)


def validate_parts(parts: Sequence[Part]) -> "list[str]":
    problems = []
    if not parts:
        problems.append("there are no parts")
    if len(parts) > MAX_PARTS:
        problems.append(f"{len(parts)} parts is more than {MAX_PARTS}")
    for part in parts:
        if part.shape not in SHAPES:
            problems.append(f"{part.name}: no shape called '{part.shape}' (shapes: {', '.join(SHAPES)})")
            continue
        values = list(part.dimensions.values()) + part.position + part.rotation + part.scale
        if not all(isinstance(v, (int, float)) and math.isfinite(v) for v in values):
            problems.append(f"{part.name}: a dimension or position is not a finite number")
            continue
        if any(v <= 0 for v in part.dimensions.values()) or any(v <= 0 for v in part.scale):
            problems.append(f"{part.name}: sizes must be positive")
        low, high = world_bounds(part)
        if np.abs(np.concatenate([low, high])).max() > MAX_EXTENT:
            problems.append(f"{part.name}: placed further than {MAX_EXTENT:g} units from the origin")
    return problems


# ---------------------------------------------------------------------------
# GLB writing and independent reading
# ---------------------------------------------------------------------------


def assemble_glb(parts: Sequence[Part], *, title: str = "model") -> bytes:
    problems = validate_parts(parts)
    if problems:
        raise Model3DError("; ".join(problems))
    binary = bytearray()
    buffer_views, accessors, meshes, materials, nodes = [], [], [], [], []

    def add_view(data: bytes, target: int) -> int:
        while len(binary) % 4:
            binary.append(0)
        buffer_views.append({"buffer": 0, "byteOffset": len(binary), "byteLength": len(data), "target": target})
        binary.extend(data)
        return len(buffer_views) - 1

    for index, part in enumerate(parts):
        vertices, normals, triangles = SHAPES[part.shape].mesh(part.dimensions)
        position_view = add_view(vertices.astype("<f4").tobytes(), 34962)
        accessors.append({"bufferView": position_view, "componentType": 5126, "count": len(vertices), "type": "VEC3",
                          "min": [float(v) for v in vertices.min(axis=0)], "max": [float(v) for v in vertices.max(axis=0)]})
        normal_view = add_view(normals.astype("<f4").tobytes(), 34962)
        accessors.append({"bufferView": normal_view, "componentType": 5126, "count": len(normals), "type": "VEC3"})
        index_view = add_view(triangles.astype("<u4").reshape(-1).tobytes(), 34963)
        accessors.append({"bufferView": index_view, "componentType": 5125, "count": int(triangles.size), "type": "SCALAR"})
        materials.append({"name": part.name, "pbrMetallicRoughness": {
            "baseColorFactor": [c / 255 for c in part.colour] + [1.0], "metallicFactor": 0.0, "roughnessFactor": 0.8}})
        meshes.append({"name": part.name, "primitives": [{
            "attributes": {"POSITION": 3 * index, "NORMAL": 3 * index + 1}, "indices": 3 * index + 2, "material": index, "mode": 4}]})
        nodes.append({"name": part.name, "mesh": index, "translation": [float(v) for v in part.position],
                      "rotation": [float(v) for v in _quaternion(part.rotation)], "scale": [float(v) for v in part.scale]})
    while len(binary) % 4:
        binary.append(0)
    document = {
        "asset": {"version": "2.0", "generator": "Alfred OS part assembly"},
        "scene": 0, "scenes": [{"name": title[:80], "nodes": list(range(len(nodes)))}],
        "nodes": nodes, "meshes": meshes, "materials": materials, "accessors": accessors,
        "bufferViews": buffer_views, "buffers": [{"byteLength": len(binary)}],
    }
    json_bytes = json.dumps(document, separators=(",", ":")).encode("utf-8")
    json_bytes += b" " * ((4 - len(json_bytes) % 4) % 4)
    total = 12 + 8 + len(json_bytes) + 8 + len(binary)
    return (struct.pack("<III", 0x46546C67, 2, total) + struct.pack("<II", len(json_bytes), 0x4E4F534A) + json_bytes
            + struct.pack("<II", len(binary), 0x004E4942) + bytes(binary))


@dataclass(frozen=True)
class Model3DReport:
    valid: bool
    problems: "tuple[str, ...]"
    meshes: int
    triangles: int
    vertices: int
    bounds_min: "tuple[float, float, float]"
    bounds_max: "tuple[float, float, float]"

    @property
    def extent(self) -> "tuple[float, float, float]":
        return tuple(hi - lo for lo, hi in zip(self.bounds_min, self.bounds_max))

    @property
    def genuinely_3d(self) -> bool:
        extent = self.extent
        return (self.valid and self.triangles > 0 and min(extent) > 0
                and min(extent) >= MIN_DEPTH_RATIO * max(extent))

    def to_dict(self) -> dict:
        return {"valid": self.valid, "genuinely_3d": self.genuinely_3d, "problems": list(self.problems), "meshes": self.meshes,
                "triangles": self.triangles, "vertices": self.vertices,
                "bounds_min": [round(v, 4) for v in self.bounds_min], "bounds_max": [round(v, 4) for v in self.bounds_max],
                "extent": [round(v, 4) for v in self.extent]}


_COMPONENTS = {"SCALAR": 1, "VEC2": 2, "VEC3": 3, "VEC4": 4, "MAT4": 16}
_COMPONENT_TYPES = {5120: "b", 5121: "B", 5122: "h", 5123: "H", 5125: "I", 5126: "f"}


def inspect_glb(data: bytes) -> Model3DReport:
    """Read a GLB from its bytes alone and measure it - the check that decides
    whether something may be called 3D. Independent of how it was made."""
    problems: "list[str]" = []
    empty = Model3DReport(False, (), 0, 0, 0, (0.0, 0.0, 0.0), (0.0, 0.0, 0.0))
    if len(data) < 28:
        return Model3DReport(False, ("too short to be a GLB",), 0, 0, 0, empty.bounds_min, empty.bounds_max)
    magic, version, length = struct.unpack_from("<III", data, 0)
    if magic != 0x46546C67 or version != 2 or length != len(data):
        return Model3DReport(False, ("not a glTF 2.0 binary (header)",), 0, 0, 0, empty.bounds_min, empty.bounds_max)
    json_length, json_type = struct.unpack_from("<II", data, 12)
    if json_type != 0x4E4F534A:
        return Model3DReport(False, ("first chunk is not JSON",), 0, 0, 0, empty.bounds_min, empty.bounds_max)
    try:
        document = json.loads(data[20:20 + json_length].decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        return Model3DReport(False, (f"JSON chunk unreadable: {exc}",), 0, 0, 0, empty.bounds_min, empty.bounds_max)
    binary = b""
    offset = 20 + json_length
    if offset + 8 <= len(data):
        bin_length, bin_type = struct.unpack_from("<II", data, offset)
        if bin_type == 0x004E4942:
            binary = data[offset + 8:offset + 8 + bin_length]
    if document.get("asset", {}).get("version") != "2.0":
        problems.append("asset.version is not 2.0")

    def read(accessor_index: int) -> np.ndarray:
        accessor = document["accessors"][accessor_index]
        view = document["bufferViews"][accessor["bufferView"]]
        width = _COMPONENTS[accessor["type"]]
        fmt = _COMPONENT_TYPES[accessor["componentType"]]
        start = view.get("byteOffset", 0) + accessor.get("byteOffset", 0)
        size = struct.calcsize(fmt) * width * accessor["count"]
        if start + size > len(binary) or start + size > view.get("byteOffset", 0) + view["byteLength"]:
            raise ValueError(f"accessor {accessor_index} reads past its buffer")
        array = np.frombuffer(binary, dtype="<" + fmt, count=width * accessor["count"], offset=start)
        return array.reshape(-1, width) if width > 1 else array

    meshes = triangles = vertices = 0
    lows, highs = [], []

    def visit(node_index: int, parent: np.ndarray) -> None:
        nonlocal meshes, triangles, vertices
        node = document["nodes"][node_index]
        local = np.eye(4)
        if "matrix" in node:
            local = np.array(node["matrix"], float).reshape(4, 4).T
        else:
            part = Part("n", "box", {}, position=list(node.get("translation", [0, 0, 0])), scale=list(node.get("scale", [1, 1, 1])))
            x, y, z, w = node.get("rotation", [0, 0, 0, 1])
            rotation = np.array([
                [1 - 2 * (y * y + z * z), 2 * (x * y - z * w), 2 * (x * z + y * w)],
                [2 * (x * y + z * w), 1 - 2 * (x * x + z * z), 2 * (y * z - x * w)],
                [2 * (x * z - y * w), 2 * (y * z + x * w), 1 - 2 * (x * x + y * y)],
            ])
            local[:3, :3] = rotation @ np.diag(part.scale)
            local[:3, 3] = part.position
        world = parent @ local
        if "mesh" in node:
            for primitive in document["meshes"][node["mesh"]]["primitives"]:
                if primitive.get("mode", 4) != 4:
                    continue
                attributes = primitive.get("attributes", {})
                if "POSITION" not in attributes:
                    problems.append(f"mesh {node['mesh']} has no positions")
                    continue
                if "NORMAL" not in attributes:
                    problems.append(f"mesh {node['mesh']} has no normals")
                positions = read(attributes["POSITION"]).astype(float)
                if "indices" in primitive:
                    indices = read(primitive["indices"]).astype(np.int64)
                    if indices.size and (indices.max() >= len(positions) or indices.min() < 0):
                        problems.append(f"mesh {node['mesh']} indexes past its vertices")
                    count = indices.size // 3
                else:
                    count = len(positions) // 3
                meshes += 1
                triangles += count
                vertices += len(positions)
                placed = (world @ np.c_[positions, np.ones(len(positions))].T).T[:, :3]
                lows.append(placed.min(axis=0))
                highs.append(placed.max(axis=0))
        for child in node.get("children", []):
            visit(child, world)

    try:
        scene = document["scenes"][document.get("scene", 0)]
        for root in scene.get("nodes", []):
            visit(root, np.eye(4))
    except (KeyError, IndexError, TypeError, ValueError) as exc:
        problems.append(f"structure: {exc}")
    if not lows:
        problems.append("no triangle mesh in the scene")
        return Model3DReport(False, tuple(problems), meshes, triangles, vertices, empty.bounds_min, empty.bounds_max)
    low, high = np.min(lows, axis=0), np.max(highs, axis=0)
    return Model3DReport(not problems, tuple(problems), meshes, triangles, vertices,
                         tuple(float(v) for v in low), tuple(float(v) for v in high))


# ---------------------------------------------------------------------------
# Describing parts in words
# ---------------------------------------------------------------------------

_NUMBER = r"(-?\d+(?:\.\d+)?)"
_COUNTS = {"a": 1, "an": 1, "one": 1, "two": 2, "three": 3, "four": 4, "five": 5, "six": 6, "seven": 7, "eight": 8,
           "nine": 9, "ten": 10, "twelve": 12, "a pair of": 2, "pair of": 2}
_NOT_NAMES = frozenset("for as with of at on in to that which called named the a an and radius height width depth length "
                       "diameter size thick tall wide long deep high sitting placed resting standing lying mounted "
                       # relation words and the pronouns that follow them ("a cube beside it")
                       "beside next alongside under underneath below beneath above atop around round inside within "
                       "behind left right through it this those these them".split())


def _colour(text: str) -> "tuple[int, int, int] | None":
    from PIL import ImageColor

    words = re.findall(r"[a-z]+", text.lower())
    for size in (2, 1):
        for index in range(len(words) - size + 1):
            try:
                return tuple(ImageColor.getrgb("".join(words[index:index + size]))[:3])
            except ValueError:
                continue
    hexed = re.search(r"#[0-9a-f]{3,6}\b", text.lower())
    return tuple(ImageColor.getrgb(hexed.group(0))[:3]) if hexed else None


def _shape_in(clause: str) -> "tuple[str, re.Match] | None":
    best = None
    for shape in SHAPES.values():
        for alias in shape.aliases:
            match = re.search(r"\b" + alias + r"(?:e?s)?\b", clause)
            if match and (best is None or match.start() < best[1].start()):
                best = (shape.name, match)
    return best


_DIMENSION_ADJECTIVES = frozenset({"tall", "high", "thick", "wide", "deep", "long"})


def _dimensions(clause: str, shape: Shape) -> "dict[str, float]":
    dims = dict(shape.defaults)
    triple = re.search(_NUMBER + r"\s*(?:x|×|by)\s*" + _NUMBER + r"\s*(?:x|×|by)\s*" + _NUMBER, clause)
    if triple and shape.name == "box":
        dims.update(width=float(triple.group(1)), height=float(triple.group(2)), depth=float(triple.group(3)))
    for word, key in (("radius", "radius"), ("height", "height"), ("tall", "height"), ("high", "height"), ("thick", "height"),
                      ("width", "width"), ("wide", "width"), ("depth", "depth"), ("deep", "depth"), ("length", "width"),
                      ("long", "width"), ("tube", "tube"), ("thickness", "tube")):
        # An adjective takes the number before it ("8 wide 0.3 tall"); a noun
        # the number after it ("height 6", "radius of 1"). Trying the noun
        # order first for an adjective read "8 wide 0.3 tall" as 0.3 wide.
        after = re.search(r"\b" + word + r"\s+(?:of\s+)?" + _NUMBER, clause)
        before = re.search(_NUMBER + r"\s*(?:units?\s+)?" + word + r"\b", clause)
        found = (before or after) if word in _DIMENSION_ADJECTIVES else (after or before)
        if found and (key in dims or (key == "height" and shape.name == "box")):
            dims[key] = abs(float(found.group(1)))
    diameter = re.search(r"\bdiameter\s+(?:of\s+)?" + _NUMBER + r"|" + _NUMBER + r"\s*(?:units?\s+)?(?:across|diameter)\b", clause)
    if diameter and "radius" in dims:
        dims["radius"] = abs(float(diameter.group(1) or diameter.group(2))) / 2
    size = re.search(r"\bsize\s+" + _NUMBER + r"|" + _NUMBER + r"\s*(?:units?\s+)?(?:on\s+each\s+side|cubed?)\b", clause)
    if size and shape.name == "box" and not triple:
        side = abs(float(size.group(1) or size.group(2)))
        dims.update(width=side, height=side, depth=side)
    return dims


def _name(clause: str, shape_match: re.Match, shape: str, taken: "set[str]") -> str:
    called = re.search(r"\b(?:called|named|as\s+the|for\s+the|forming\s+the|as\s+its|for\s+its)\s+([a-z][a-z\s-]{1,30}?)(?=\s*(?:,|$|\bradius\b|\bheight\b|\bwidth\b|\bdepth\b|\bon\b|\bat\b|\bbeside\b|\bunder\b|\bbelow\b|\babove\b|\baround\b|\bnext\b|\bleft\b|\bright\b|\bin\b|\bbehind\b|\d))", clause)
    words = []
    if called:
        words = [w for w in called.group(1).split() if w not in _NOT_NAMES]
    else:
        after = clause[shape_match.end():].split()
        for word in after:
            if word in _NOT_NAMES or re.match(r"-?\d", word) or word in ("x", "×", "by"):
                break
            words.append(word)
            if len(words) == 2:
                break
    base = " ".join(words).strip(" ,.") or shape
    name, n = base, 2
    while name in taken:
        name, n = f"{base} {n}", n + 1
    return name


_RELATIONS = (
    ("on_top", r"\b(?:on\s+top\s+of|on|above|atop|sitting\s+on|resting\s+on|mounted\s+on|stacked\s+on)\s+(?:the\s+)?"),
    ("below", r"\b(?:under(?:neath)?|below|beneath)\s+(?:the\s+)?"),
    ("corners", r"\bat\s+(?:the\s+)?(?:four\s+)?(?:bottom\s+)?corners\s+of\s+(?:the\s+)?"),
    ("around", r"\b(?:around|round|encircling|circling)\s+(?:the\s+)?"),
    ("left", r"\b(?:to\s+the\s+)?left\s+of\s+(?:the\s+)?"),
    ("right", r"\b(?:to\s+the\s+)?right\s+of\s+(?:the\s+)?|\b(?:beside|next\s+to|alongside)\s+(?:the\s+)?"),
    ("front", r"\bin\s+front\s+of\s+(?:the\s+)?"),
    ("behind", r"\bbehind\s+(?:the\s+)?"),
    ("inside", r"\b(?:inside|within|in\s+the\s+middle\s+of|centred\s+on|centered\s+on|through)\s+(?:the\s+)?"),
    ("each_end", r"\bat\s+(?:each|both)\s+ends?\s+of\s+(?:the\s+)?"),
)


# "on top of it", "under that one" - a word standing for the part described
# just before, which is how people describe a stack in one breath.
_PRONOUN_TARGET = re.compile(r"(?:it|that(?:\s+one)?|this(?:\s+one)?|them|those|these)\b")


def _relation(clause: str, known: Sequence[str]) -> "tuple[str, str] | None":
    """The relation a clause names and the earlier part it is relative to -
    by that part's name, or by a pronoun meaning the part described last."""
    for kind, pattern in _RELATIONS:
        for match in re.finditer(pattern, clause):
            rest = clause[match.end():]
            target = next((name for name in sorted(known, key=len, reverse=True) if re.match(re.escape(name) + r"\b", rest)), None)
            if target is None and known and _PRONOUN_TARGET.match(rest):
                target = known[-1]
            if target is not None:
                return kind, target
    return None


def _count(clause: str) -> int:
    lead = re.match(r"^\s*(a\s+pair\s+of|pair\s+of|\d+|[a-z]+)\b", clause)
    if not lead:
        return 1
    word = lead.group(1)
    if word.isdigit():
        return max(1, min(int(word), 50))
    return _COUNTS.get(" ".join(word.split()), 1)


def _rotation(clause: str) -> "list[float]":
    rotation = [0.0, 0.0, 0.0]
    for match in re.finditer(r"\b(?:rotated|turned|tilted)\s+" + _NUMBER + r"\s*(?:°|degrees?)\s*(?:about|around|on)?\s*(?:the\s+)?([xyz])?", clause):
        axis = "xyz".index(match.group(2)) if match.group(2) else 1
        rotation[axis] += float(match.group(1))
    if re.search(r"\b(?:lying\s+(?:on\s+its\s+side|flat|down)|on\s+its\s+side|horizontal(?:ly)?|sideways)\b", clause):
        rotation[2] += 90.0
    if re.search(r"\b(?:upside\s+down|pointing\s+down|inverted)\b", clause):
        rotation[0] += 180.0
    return rotation


def _explicit_position(clause: str) -> "list[float] | None":
    found = re.search(r"\bat\s*\(?\s*" + _NUMBER + r"\s*,\s*" + _NUMBER + r"\s*,\s*" + _NUMBER + r"\s*\)?", clause)
    return [float(found.group(i)) for i in (1, 2, 3)] if found else None


@dataclass(frozen=True)
class PartDescription:
    parts: "tuple[Part, ...]"
    not_understood: "tuple[str, ...]"


_SPLIT = re.compile(r"\s*(?:;|\.\s+|,\s*(?:and\s+)?(?=(?:a|an|one|two|three|four|five|six|seven|eight|nine|ten|twelve|\d+|pair|some|several|many)\b)|\s+and\s+(?=(?:a|an|one|two|three|four|five|six|seven|eight|nine|ten|twelve|\d+|pair|some|several|many)\b)|\s+with\s+(?=(?:a|an|one|two|three|four|five|six|seven|eight|nine|ten|twelve|\d+|pair|some|several|many)\b))\s*", re.IGNORECASE)


def parse_part_description(text: str) -> PartDescription:
    """Parts from a description - each clause one kind of part, placed by the
    relation it names to a part described before it. A clause naming no shape is
    reported, never guessed into one."""
    body = str(text or "")
    if ":" in body:
        body = body.split(":", 1)[1]
    parts: "list[Part]" = []
    unknown: "list[str]" = []
    for raw in _SPLIT.split(body):
        clause = " ".join(raw.lower().split()).strip(" ,.!?")
        if not clause:
            continue
        found = _shape_in(clause)
        if found is None:
            # "make a 3D water tower" is the request naming its subject, not a part
            if re.search(r"\b(?:3d|3-d|three[\s-]dimensional)\b", clause):
                continue
            if re.search(r"[a-z]{3,}", clause):
                unknown.append(raw.strip(" ,.!?"))
            continue
        shape_name, match = found
        shape = SHAPES[shape_name]
        count = _count(clause)
        dims = _dimensions(clause, shape)
        colour = _colour(clause[: match.start()] + " " + clause[match.end():]) or (200, 200, 200)
        rotation = _rotation(clause)
        relation = _relation(clause, [p.name for p in parts])
        explicit = _explicit_position(clause)
        name = _name(clause, match, shape_name, {p.name for p in parts})
        group = []
        for k in range(count):
            part_name = name if count == 1 else f"{name} {k + 1}"
            group.append(Part(part_name, shape_name, dict(dims), colour, rotation=list(rotation)))
        _place(group, parts, relation, explicit, clause)
        parts.extend(group)
    return PartDescription(tuple(parts), tuple(unknown))


def _place(group: "list[Part]", existing: "list[Part]", relation, explicit, clause: str) -> None:
    def extents(part):
        low, high = world_bounds(Part(part.name, part.shape, part.dimensions, rotation=part.rotation, scale=part.scale))
        return low, high

    if explicit is not None:
        for index, part in enumerate(group):
            part.position = [explicit[0] + index * 0.0, explicit[1], explicit[2]]
            part.note = "at the position given"
        return
    if relation is None:
        if not existing:
            if len(group) > 1:
                _row(group, None)
            return
        anchor = existing[-1]
        relation = ("right", anchor.name)
        for part in group:
            part.note = f"no position given - placed beside {anchor.name}"
    kind, target_name = relation
    target = next(p for p in existing if p.name == target_name)
    t_low, t_high = world_bounds(target)
    t_centre = (t_low + t_high) / 2
    for index, part in enumerate(group):
        low, high = extents(part)
        half = (high - low) / 2
        centre_offset = -(low + high) / 2  # a rotated part's own bounds may not be centred
        position = t_centre.copy()
        if kind == "on_top":
            position[1] = t_high[1] + half[1]
        elif kind == "below":
            position[1] = t_low[1] - half[1]
        elif kind in ("left", "right"):
            sign = -1 if kind == "left" else 1
            position[0] = (t_low[0] - half[0] if sign < 0 else t_high[0] + half[0])
        elif kind in ("front", "behind"):
            position[2] = t_high[2] + half[2] if kind == "front" else t_low[2] - half[2]
        elif kind == "corners":
            corners = [(sx, sz) for sx in (-1, 1) for sz in (-1, 1)]
            sx, sz = corners[index % 4]
            # beneath the corners, touching the underside - legs, feet, supports
            position = np.array([t_centre[0] + sx * (t_high[0] - t_centre[0]), t_low[1] - half[1], t_centre[2] + sz * (t_high[2] - t_centre[2])])
        elif kind == "around" and len(group) == 1:
            position = t_centre.copy()  # one part around another encircles it
        elif kind == "around":
            angle = 2 * math.pi * index / max(len(group), 1)
            radius = max(t_high[0] - t_centre[0], t_high[2] - t_centre[2]) + max(half[0], half[2])
            position = np.array([t_centre[0] + radius * math.cos(angle), t_centre[1], t_centre[2] + radius * math.sin(angle)])
        elif kind == "each_end":
            axis = int(np.argmax(t_high - t_low))
            position[axis] = (t_low if index % 2 == 0 else t_high)[axis]
        if kind in ("on_top", "below", "left", "right", "front", "behind") and len(group) > 1:
            spread_axis = 0 if kind not in ("left", "right") else 2
            span = (t_high - t_low)[spread_axis]
            position[spread_axis] = t_low[spread_axis] + span * (index + 0.5) / len(group)
        part.position = [float(v) for v in np.asarray(position) + centre_offset]
        if not part.note:
            part.note = f"{kind.replace('_', ' ')} {target_name}"


def _row(group, _anchor) -> None:
    offset = 0.0
    for part in group:
        low, high = world_bounds(part)
        width = (high - low)[0]
        part.position = [offset + width / 2, 0.0, 0.0]
        offset += width * 1.25


def shape_grammar() -> str:
    return (
        "Describe the subject as parts, separated by ' ; ', each like: '<count> <colour> <shape> <part name> "
        "<dimensions> <placement>'. Shapes: " + "; ".join(f"{s.name} ({', '.join(s.aliases[:4])})" for s in SHAPES.values())
        + ". Dimensions: box W x H x D (or width/height/depth N), sphere radius N, cylinder/cone radius N height N, "
        "torus radius N tube N. Placement relative to an earlier part: on top of the <part>, under the <part>, "
        "left of / right of / in front of / behind the <part>, around the <part>, at the corners of the <part>, "
        "at each end of the <part>, inside the <part>, or at (x, y, z). Optional: rotated N degrees about x|y|z, lying on its side."
    )
