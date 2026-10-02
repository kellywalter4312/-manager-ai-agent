"""The perception contract (GM-03).

A **detection** is a region of one frame: a box in normalised coordinates
(0..1 of width and height, so it means the same at any resolution), how
confident the backend is, and - only when the backend can honestly say - what
it is (``label``). An empty label is not a failure: it is the truthful answer
of a backend that finds *that* something is there without claiming *what*.

A **provider** turns a sequence of frames into one ``FramePerception`` per
frame. A sequence, not a single picture, because some ways of seeing need
more than one moment (motion needs a background); a still is a sequence of
one. What happens next - following regions over time, counting, deciding what
an arrival means - belongs to ``src/services/perception``, the same for every
backend.
"""

from __future__ import annotations

import io
from abc import ABC, abstractmethod
from dataclasses import dataclass
from typing import Sequence


class PerceptionError(RuntimeError):
    """A plain, person-facing reason perception could not be done."""


@dataclass(frozen=True)
class Frame:
    """One picture to perceive, and when it was taken (seconds into the
    source, or since the listen began)."""

    at_seconds: float
    image_bytes: bytes


@dataclass(frozen=True)
class Detection:
    box: "tuple[float, float, float, float]"   # x0, y0, x1, y1 in 0..1
    confidence: float
    label: str = ""
    # What the region looks like, as its mean colour (0..255 per channel) -
    # the one appearance cue every backend can give, so following two things
    # whose paths cross does not swap them. Empty when a backend cannot say.
    signature: "tuple[float, ...]" = ()

    def __post_init__(self) -> None:
        x0, y0, x1, y1 = (min(1.0, max(0.0, float(v))) for v in self.box)
        if x1 <= x0 or y1 <= y0:
            raise ValueError("a detection's box must have width and height")
        object.__setattr__(self, "box", (x0, y0, x1, y1))
        object.__setattr__(self, "confidence", min(1.0, max(0.0, float(self.confidence))))
        object.__setattr__(self, "label", " ".join(str(self.label or "").split()).lower())
        object.__setattr__(self, "signature", tuple(round(float(v), 2) for v in self.signature))

    def looks_like(self, other: "Detection") -> float:
        """1.0 for the same appearance, falling towards 0 as the two regions'
        colours part; 1.0 when either cannot say (no evidence either way)."""
        if not self.signature or not other.signature or len(self.signature) != len(other.signature):
            return 1.0
        distance = sum((a - b) ** 2 for a, b in zip(self.signature, other.signature)) ** 0.5
        return max(0.0, 1.0 - distance / 160.0)

    @property
    def center(self) -> "tuple[float, float]":
        x0, y0, x1, y1 = self.box
        return ((x0 + x1) / 2.0, (y0 + y1) / 2.0)

    @property
    def area(self) -> float:
        x0, y0, x1, y1 = self.box
        return (x1 - x0) * (y1 - y0)

    def to_dict(self) -> dict:
        return {"box": [round(v, 4) for v in self.box], "confidence": round(self.confidence, 3), "label": self.label}


@dataclass(frozen=True)
class FramePerception:
    at_seconds: float
    width: int
    height: int
    detections: "tuple[Detection, ...]"


class PerceptionProvider(ABC):
    """One way of seeing - selected by name, replaceable."""

    @property
    @abstractmethod
    def name(self) -> str:
        ...

    @property
    @abstractmethod
    def describes(self) -> str:
        """A short, plain account of what this backend can and cannot say."""

    @property
    def labels_objects(self) -> bool:
        """Whether this backend names what it finds (fills ``label``)."""
        return False

    @property
    def needs_sequence(self) -> bool:
        """Whether one frame alone is too little for this way of seeing."""
        return False

    @abstractmethod
    def perceive(self, frames: Sequence[Frame]) -> "tuple[FramePerception, ...]":
        """One perception per frame, in order. Raises ``PerceptionError``
        with a plain reason when it cannot see at all."""


def open_image(image_bytes: bytes):
    from PIL import Image

    try:
        image = Image.open(io.BytesIO(image_bytes))
        image.load()
    except Exception as exc:  # noqa: BLE001 - any undecodable picture is the same honest failure
        raise PerceptionError(f"that is not a picture I can read ({type(exc).__name__})") from exc
    return image.convert("RGB")


def grey_array(image, width: int):
    """``image`` scaled to ``width`` (aspect kept), as a float grey array."""
    import numpy as np

    height = max(1, round(image.height * width / max(1, image.width)))
    small = image.resize((width, height))
    return np.asarray(small.convert("L"), dtype=np.float32)


def components(mask, *, min_pixels: int) -> "list[tuple[int, int, int, int, int]]":
    """Connected regions of a boolean mask (8-connected) as
    (x0, y0, x1, y1, pixel count), smallest regions dropped - a scan-line
    flood fill over the (small) mask, no image library needed."""
    import numpy as np

    height, width = mask.shape
    seen = np.zeros_like(mask, dtype=bool)
    found = []
    ys, xs = np.nonzero(mask)
    for start_y, start_x in zip(ys.tolist(), xs.tolist()):
        if seen[start_y, start_x]:
            continue
        stack = [(start_y, start_x)]
        seen[start_y, start_x] = True
        x0 = x1 = start_x
        y0 = y1 = start_y
        count = 0
        while stack:
            y, x = stack.pop()
            count += 1
            x0, x1, y0, y1 = min(x0, x), max(x1, x), min(y0, y), max(y1, y)
            for dy in (-1, 0, 1):
                ny = y + dy
                if ny < 0 or ny >= height:
                    continue
                for dx in (-1, 0, 1):
                    nx = x + dx
                    if 0 <= nx < width and mask[ny, nx] and not seen[ny, nx]:
                        seen[ny, nx] = True
                        stack.append((ny, nx))
        if count >= min_pixels:
            found.append((x0, y0, x1 + 1, y1 + 1, count))
    return found


def clean(mask, iterations: int = 1):
    """Open then close a boolean mask with a 3x3 neighbourhood - speckle out,
    small holes filled - with numpy shifts only."""
    import numpy as np

    def dilate(m):
        out = m.copy()
        out[1:, :] |= m[:-1, :]
        out[:-1, :] |= m[1:, :]
        out[:, 1:] |= m[:, :-1]
        out[:, :-1] |= m[:, 1:]
        return out

    def erode(m):
        out = m.copy()
        out[1:, :] &= m[:-1, :]
        out[:-1, :] &= m[1:, :]
        out[:, 1:] &= m[:, :-1]
        out[:, :-1] &= m[:, 1:]
        return out

    m = mask.astype(bool)
    for _ in range(iterations):
        m = dilate(erode(m))
    for _ in range(iterations):
        m = erode(dilate(m))
    return m if isinstance(m, np.ndarray) else np.asarray(m)
