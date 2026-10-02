"""The perception provider axis (GM-03) - selected by name from
``Settings.perception``, replaceable like every other axis, governed through
``provider perception_provider use <backend>``.

    motion        moving regions (default) - deterministic, no model
    regions       regions standing out in a still - deterministic, no model
    local_vision  either, each thing named by the local vision model
    null          no perception - every request is refused honestly

A new way of seeing (a trained detector, a vendor's API, a depth camera's own
segmentation) is one more entry here and one more class beside these; nothing
that uses perception changes.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any, Sequence

from src.providers.perception.contract import Frame, FramePerception, PerceptionError, PerceptionProvider
from src.providers.perception.labelled import LabelledPerception
from src.providers.perception.motion import MotionPerception
from src.providers.perception.regions import RegionPerception


class NullPerception(PerceptionProvider):
    @property
    def name(self) -> str:
        return "null"

    @property
    def describes(self) -> str:
        return "no perception is configured"

    def perceive(self, frames: Sequence[Frame]) -> "tuple[FramePerception, ...]":
        raise PerceptionError("no perception is configured - choose one with 'provider perception_provider use <backend>'")


class AdaptivePerception(PerceptionProvider):
    """The default: motion for a sequence, regions for a still - so one
    choice serves a recording, a live camera and a photograph alike."""

    def __init__(self) -> None:
        self._motion = MotionPerception()
        self._regions = RegionPerception()

    @property
    def name(self) -> str:
        return "motion"

    @property
    def describes(self) -> str:
        return "moving regions in a sequence (regions standing out, for a still) - where, never what"

    def perceive(self, frames: Sequence[Frame]) -> "tuple[FramePerception, ...]":
        return (self._motion if len(frames) >= 3 else self._regions).perceive(frames)


def available_perception_backends() -> "list[str]":
    return ["motion", "regions", "local_vision", "null"]


def build_perception(settings: Any) -> PerceptionProvider:
    choice = str(getattr(settings, "perception", "motion") or "motion").strip().lower()
    if choice == "motion":
        return AdaptivePerception()
    if choice == "regions":
        return RegionPerception()
    if choice == "null":
        return NullPerception()
    if choice == "local_vision":
        vision = None
        model = Path(getattr(settings, "local_vision_model_path", "") or "")
        projector = Path(getattr(settings, "local_vision_projector_path", "") or "")
        if model.is_file() and projector.is_file():
            from src.providers.local_llm_provider import LocalLLMProvider

            vision = LocalLLMProvider(
                model_path=getattr(settings, "local_model_path", ""), max_tokens=24,
                vision_model_path=str(model), vision_projector_path=str(projector),
            )
        return LabelledPerception(RegionPerception(), MotionPerception(), vision)
    raise ValueError(f"Unknown perception backend {choice!r}. Available: {', '.join(available_perception_backends())}")
