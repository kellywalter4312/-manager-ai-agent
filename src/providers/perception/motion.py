"""The "motion" perception backend (GM-03): things that move, or that moved
into the scene and stayed - for any subject, because nothing about *what*
moves is assumed.

**Colour, not brightness.** A red vehicle on grey ground can have almost the
same brightness as the ground; a difference in colour is still a difference.
Each pixel's distance from the background is measured in colour.

**A background that only learns what is empty.** It starts as the per-pixel
median of the opening frames (which removes anything merely passing through
them) and then adapts slowly - but only where nothing was found. So something
that arrives and stops stays found, as a stopped thing, for as long as it
stands there; and when it leaves, the place it stood was never learned as
occupied, so no "ghost" is left behind. (The one case this cannot tell apart
is a thing already standing still when the recording begins: until it moves,
it is part of the scene.)

**Light is not a thing.** A cloud's shadow, a light switched on, the sun
coming out: the ground changes brightness but keeps its own colour. A pixel
whose chromaticity (its colour with brightness divided out) still matches the
background, and whose brightness changed only within a shadow-to-sunlight
band, is a change of light and is not found. What this cannot tell apart is a
thing with exactly the ground's hue and only slightly darker or lighter than
it - to one pixel, that is what a shadow is.

**One thing, one region.** Found pixels are cleaned of speckle and then
joined across small gaps before being grouped, so a shape whose colours vary
(a body and its wings, a vehicle and its load) is one region, not several.

Each region is a detection with no label: *that* something is there, and
where - never what.
"""

from __future__ import annotations

from typing import Sequence

from src.providers.perception.contract import (
    Detection,
    Frame,
    FramePerception,
    PerceptionError,
    PerceptionProvider,
    clean,
    components,
    open_image,
)

WORK_WIDTH = 192
MIN_REGION_FRACTION = 0.0015
LEARNING_RATE = 0.05
JOIN_PIXELS = 2
# How much darker (a shadow) or lighter (sunlight) the ground can become and
# still be the same ground, when its hue has not changed.
LIGHT_BAND = (0.5, 1.45)


def _chroma(colour):
    """Colour with brightness divided out - what a change of light keeps."""
    import numpy as np

    total = colour.sum(axis=-1, keepdims=True)
    return colour / np.maximum(total, 1.0)


def _brightness(colour):
    return colour.sum(axis=-1)


def _dilate(mask, times: int):
    out = mask.copy()
    for _ in range(times):
        grown = out.copy()
        grown[1:, :] |= out[:-1, :]
        grown[:-1, :] |= out[1:, :]
        grown[:, 1:] |= out[:, :-1]
        grown[:, :-1] |= out[:, 1:]
        out = grown
    return out


class MotionPerception(PerceptionProvider):
    def __init__(self, *, work_width: int = WORK_WIDTH, min_region_fraction: float = MIN_REGION_FRACTION,
                 learning_rate: float = LEARNING_RATE) -> None:
        self._width = int(work_width)
        self._min_fraction = float(min_region_fraction)
        self._rate = float(learning_rate)

    @property
    def name(self) -> str:
        return "motion"

    @property
    def describes(self) -> str:
        return "things that move, or moved in and stayed, against a background of what is empty - where, never what"

    @property
    def needs_sequence(self) -> bool:
        return True

    @staticmethod
    def _is_ghost(colour, found, x0: int, y0: int, x1: int, y1: int, threshold: float) -> bool:
        """Whether the pixels found in a region look like the band of ground
        around it - the mark a remembered-but-departed thing leaves, not a
        thing. Only the found pixels are compared: a region's box also holds
        ground wherever the thing's shape is not rectangular."""
        import numpy as np

        height, width = colour.shape[:2]
        pad = max(2, min(x1 - x0, y1 - y0) // 4)
        ox0, oy0, ox1, oy1 = max(0, x0 - pad), max(0, y0 - pad), min(width, x1 + pad), min(height, y1 + pad)
        ring = np.ones((oy1 - oy0, ox1 - ox0), dtype=bool)
        ring[y0 - oy0:y1 - oy0, x0 - ox0:x1 - ox0] = False
        region = found[y0:y1, x0:x1]
        if not ring.any() or not region.any():
            return False
        inside = colour[y0:y1, x0:x1][region]
        around = colour[oy0:oy1, ox0:ox1][ring]
        return float(np.linalg.norm(np.median(inside, axis=0) - np.median(around, axis=0))) < threshold * 0.5

    def perceive(self, frames: Sequence[Frame]) -> "tuple[FramePerception, ...]":
        import numpy as np

        if len(frames) < 3:
            raise PerceptionError("seeing motion needs at least three moments of the scene")
        images = [open_image(f.image_bytes) for f in frames]
        width, height = images[0].size
        small_height = max(1, round(height * self._width / max(1, width)))
        colours = np.stack([np.asarray(image.resize((self._width, small_height)), dtype=np.float32) for image in images])
        opening = max(3, min(10, len(frames) // 6))
        background = np.median(colours[:opening], axis=0)
        noise = float(np.median(np.sqrt(((colours[:opening] - background) ** 2).sum(axis=3))))
        threshold = max(30.0, noise * 4.0)
        # How far chromaticity wanders on its own in this recording - the
        # sensor's noise in hue, measured, never assumed.
        chroma_noise = float(np.median(np.sqrt(((_chroma(colours[:opening]) - _chroma(background)) ** 2).sum(axis=3))))
        chroma_threshold = max(0.03, chroma_noise * 4.0)
        min_pixels = max(4, int(self._width * small_height * self._min_fraction))
        results = []
        for frame, colour in zip(frames, colours):
            distance = np.sqrt(((colour - background) ** 2).sum(axis=2))
            ratio = _brightness(colour) / np.maximum(_brightness(background), 1.0)
            same_hue = np.sqrt(((_chroma(colour) - _chroma(background)) ** 2).sum(axis=2)) < chroma_threshold
            light_only = same_hue & (ratio > LIGHT_BAND[0]) & (ratio < LIGHT_BAND[1])
            found = clean((distance > threshold) & ~light_only)
            joined = _dilate(found, JOIN_PIXELS)
            detections = []
            for x0, y0, x1, y1, _count in components(joined, min_pixels=min_pixels):
                if self._is_ghost(colour, found, x0, y0, x1, y1, threshold):
                    # What the background remembered is no longer there, and
                    # what is there looks like its own surroundings: the model
                    # was wrong, not the scene. Learn it as empty.
                    background[y0:y1, x0:x1] = colour[y0:y1, x0:x1]
                    joined[y0:y1, x0:x1] = False
                    continue
                # Shrink back to the found pixels the join grew from.
                region = found[y0:y1, x0:x1]
                if not region.any():
                    continue
                ys, xs = np.nonzero(region)
                bx0, bx1 = x0 + int(xs.min()), x0 + int(xs.max()) + 1
                by0, by1 = y0 + int(ys.min()), y0 + int(ys.max()) + 1
                if (bx1 - bx0) * (by1 - by0) < min_pixels:
                    continue
                strength = float(np.mean(distance[by0:by1, bx0:bx1]))
                inside = found[by0:by1, bx0:bx1]
                detections.append(Detection(
                    box=(bx0 / self._width, by0 / small_height, bx1 / self._width, by1 / small_height),
                    confidence=min(1.0, 0.4 + 0.6 * min(1.0, strength / (threshold * 3))),
                    signature=tuple(np.mean(colour[by0:by1, bx0:bx1][inside], axis=0).tolist()),
                ))
            results.append(FramePerception(at_seconds=frame.at_seconds, width=width, height=height,
                                           detections=tuple(detections)))
            # Learn only what is empty: where something was found, the
            # background keeps what it knew.
            empty = ~joined
            background[empty] = (1 - self._rate) * background[empty] + self._rate * colour[empty]
        return tuple(results)
