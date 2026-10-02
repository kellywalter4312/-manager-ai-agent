"""The "regions" perception backend (GM-03): regions of a still that stand
out from their background - objects on a surface, marks on a board, shapes in
a field - for any subject, because nothing about *what* stands out is assumed.

The background is estimated from the picture's own border (the median colour
of its outermost band, which is the surface things lie on); every pixel far
enough from it, cleaned of speckle, is split into connected regions. Each is a
detection with no label. In a sequence each frame is seen on its own, so this
backend also serves a camera looking at something that does not move.
"""

from __future__ import annotations

from typing import Sequence

from src.providers.perception.contract import (
    Detection,
    Frame,
    FramePerception,
    PerceptionProvider,
    clean,
    components,
    open_image,
)

WORK_WIDTH = 256
MIN_REGION_FRACTION = 0.0008


class RegionPerception(PerceptionProvider):
    def __init__(self, *, work_width: int = WORK_WIDTH, min_region_fraction: float = MIN_REGION_FRACTION) -> None:
        self._width = int(work_width)
        self._min_fraction = float(min_region_fraction)

    @property
    def name(self) -> str:
        return "regions"

    @property
    def describes(self) -> str:
        return "regions that stand out from the picture's own background - where, never what"

    def perceive(self, frames: Sequence[Frame]) -> "tuple[FramePerception, ...]":
        import numpy as np

        results = []
        for frame in frames:
            image = open_image(frame.image_bytes)
            width, height = image.size
            small_height = max(1, round(height * self._width / max(1, width)))
            pixels = np.asarray(image.resize((self._width, small_height)), dtype=np.float32)
            band = max(1, min(pixels.shape[0], pixels.shape[1]) // 20)
            border = np.concatenate([
                pixels[:band].reshape(-1, 3), pixels[-band:].reshape(-1, 3),
                pixels[:, :band].reshape(-1, 3), pixels[:, -band:].reshape(-1, 3),
            ])
            background = np.median(border, axis=0)
            distance = np.sqrt(((pixels - background) ** 2).sum(axis=2))
            border_distance = np.sqrt(((border - background) ** 2).sum(axis=1))
            threshold = max(40.0, float(np.percentile(border_distance, 95)) * 2.5)
            mask = clean(distance > threshold)
            min_pixels = max(4, int(mask.size * self._min_fraction))
            detections = []
            for x0, y0, x1, y1, count in components(mask, min_pixels=min_pixels):
                if (x1 - x0) * (y1 - y0) > 0.9 * mask.size:
                    continue  # the whole picture is not a region in it
                strength = float(np.mean(distance[y0:y1, x0:x1]))
                inside = mask[y0:y1, x0:x1]
                detections.append(Detection(
                    box=(x0 / mask.shape[1], y0 / mask.shape[0], x1 / mask.shape[1], y1 / mask.shape[0]),
                    confidence=min(1.0, 0.4 + 0.6 * min(1.0, strength / (threshold * 3))),
                    signature=tuple(np.mean(pixels[y0:y1, x0:x1][inside], axis=0).tolist()) if inside.any() else (),
                ))
            results.append(FramePerception(at_seconds=frame.at_seconds, width=width, height=height,
                                           detections=tuple(detections)))
        return tuple(results)
