"""Post-Pre-Stage-9 D3 - Image Intelligence in Half Brain.

The Brain is asked first; when it has no view, the attributed local vision model
describes the image; with neither, the honest refusal is unchanged. In-process
doubles only - the real local vision model is verified outside the suite.
"""

from __future__ import annotations

from pathlib import Path

from src.core.contracts import Task
from src.manager.brain import BrainAction, BrainDecision
from src.manager.capabilities import CapabilityRequest, image_analysis
from src.services.identity.identity import AgentIdentity

PNG = bytes.fromhex(
    "89504e470d0a1a0a0000000d4948445200000001000000010806000000"
    "1f15c4890000000d49444154789c6360000002000154a24f5d0000000049454e44ae426082"
)


class FakeVision:
    def __init__(self, text="A red bicycle leaning on a white wall."):
        self.text = text
        self.asked = []

    def describe_image(self, image, prompt):
        self.asked.append((image, prompt))
        return self.text


def _request(tmp_path: Path, description: str, *, analyze_image=None, assist=None) -> CapabilityRequest:
    return CapabilityRequest(
        task=Task(description=description),
        identity=AgentIdentity(name="Alfred", capabilities=frozenset({"vision"})),
        roster=lambda: (), own_name="Alfred", own_capabilities=(),
        analyze_image=analyze_image, assist=assist,
    )


def _image(tmp_path: Path) -> Path:
    path = tmp_path / "photo.png"
    path.write_bytes(PNG)
    return path


def test_half_brain_describes_an_image_through_the_attributed_local_vision_model(tmp_path):
    vision = FakeVision()
    image = _image(tmp_path)
    result = image_analysis(_request(tmp_path, f"describe this image {image}", assist=vision))
    assert result.succeeded and result.output.startswith("A red bicycle")
    assert "small local vision model" in result.output and "not verified" in result.output
    assert result.payload == {"handled_by": "image_analysis", "brain": "local_vision", "assisted": True}
    assert vision.asked[0][0] == PNG


def test_a_brain_that_can_see_still_answers_first(tmp_path):
    vision = FakeVision()
    image = _image(tmp_path)

    def analyze(image_bytes, prompt):
        return BrainDecision(action=BrainAction.REPORT, reason="A bicycle, seen by the Brain.", confidence=0.9, source="vendor")

    result = image_analysis(_request(tmp_path, f"what's in this image {image}", analyze_image=analyze, assist=vision))
    assert result.output == "A bicycle, seen by the Brain." and not vision.asked


def test_a_brain_with_no_view_falls_back_to_local_vision(tmp_path):
    vision = FakeVision()
    image = _image(tmp_path)

    def no_view(image_bytes, prompt):
        return BrainDecision.no_view(reason="no reasoning capability is configured", source="null")

    result = image_analysis(_request(tmp_path, f"describe this picture {image}", analyze_image=no_view, assist=vision))
    assert result.succeeded and result.payload["brain"] == "local_vision"


def test_the_persons_own_question_is_what_the_vision_model_is_asked(tmp_path):
    vision = FakeVision("Blue.")
    image = _image(tmp_path)
    image_analysis(_request(tmp_path, f"what colour is the bicycle in {image}", assist=vision))
    assert "what colour is the bicycle" in vision.asked[0][1]


def test_with_neither_a_seeing_brain_nor_local_vision_it_refuses_honestly(tmp_path):
    image = _image(tmp_path)
    result = image_analysis(_request(tmp_path, f"describe this image {image}"))
    assert not result.succeeded and "no vision-capable reasoning" in result.error.lower()


def test_a_local_vision_model_that_sees_nothing_is_reported_not_invented(tmp_path):
    image = _image(tmp_path)
    result = image_analysis(_request(tmp_path, f"describe this image {image}", assist=FakeVision("")))
    assert not result.succeeded and "couldn't analyze" in result.error
