"""The "local_vision" perception backend (GM-03): regions found by a
deterministic backend, each then *named* by the local vision model - so a
detection can say what it is, not only where.

Naming is the expensive part (a local vision model on a laptop CPU takes tens
of seconds per look), so it is asked once per thing, not once per frame: the
analysis that follows regions over time (``src/services/perception``) calls
``label`` for each tracked thing's clearest view. When no vision model is
installed this backend says so plainly and never invents a name.
"""

from __future__ import annotations

import io
import re
from typing import Any, Sequence

from src.providers.perception.contract import (
    Frame,
    FramePerception,
    PerceptionError,
    PerceptionProvider,
    open_image,
)

_PROMPT = "Name the main thing in this picture in one to three words. Reply with only the name."


class LabelledPerception(PerceptionProvider):
    def __init__(self, base_still: PerceptionProvider, base_sequence: PerceptionProvider, vision: Any) -> None:
        self._still = base_still
        self._sequence = base_sequence
        self._vision = vision

    @property
    def name(self) -> str:
        return "local_vision"

    @property
    def describes(self) -> str:
        where = "regions (or motion)"
        if self._vision is None:
            return f"{where}; naming them needs the local vision model, which is not installed"
        return f"{where}, each named by the local vision model - slow on this machine, asked once per thing"

    @property
    def labels_objects(self) -> bool:
        return self._vision is not None

    def perceive(self, frames: Sequence[Frame]) -> "tuple[FramePerception, ...]":
        base = self._sequence if len(frames) >= 3 else self._still
        return base.perceive(frames)

    def label(self, image_bytes: bytes, box: "tuple[float, float, float, float]") -> str:
        """What the thing in ``box`` of the picture is, in a few words - or ""
        when the model could not say. Never a guess of this code's own."""
        if self._vision is None:
            raise PerceptionError("naming what is seen needs the local vision model, which is not installed")
        image = open_image(image_bytes)
        width, height = image.size
        x0, y0, x1, y1 = box
        pad_x, pad_y = (x1 - x0) * 0.15, (y1 - y0) * 0.15
        crop = image.crop((max(0, int((x0 - pad_x) * width)), max(0, int((y0 - pad_y) * height)),
                           min(width, int((x1 + pad_x) * width)), min(height, int((y1 + pad_y) * height))))
        if crop.width < 8 or crop.height < 8:
            crop = crop.resize((max(32, crop.width * 4), max(32, crop.height * 4)))
        buffer = io.BytesIO()
        crop.save(buffer, "PNG")
        try:
            completion = self._vision.analyze_image(buffer.getvalue(), _PROMPT)
        except Exception as exc:  # noqa: BLE001 - a model that fails is reported, never replaced by a guess
            raise PerceptionError(f"the vision model could not look ({type(exc).__name__})") from exc
        text = (getattr(completion, "text", "") or "").strip().lower()
        text = re.sub(r"[^a-z0-9 \-]", " ", text)
        words = [w for w in text.split() if w not in ("a", "an", "the", "this", "is", "it")][:3]
        return " ".join(words)
