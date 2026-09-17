"""Whole-ecosystem closure campaign 2 - instruction-based image editing.

A person's own words become an ordered plan of parametric edits
(``src/providers/media/image_edits.py``), each applied to real pixels and then
verified by measurement; the result is a new image derived from the source,
which is never touched. Clauses no operation reads are reported, never dropped:
a Brain may restate the instruction in the edit vocabulary (read again by the
same parser), otherwise the request is refused with nothing created. The served
run against a real uploaded photo is recorded in REQUIREMENT_LEDGER.md.
"""

from __future__ import annotations

import base64
import io
import json

import pytest
from PIL import Image, ImageDraw, ImageStat

import scripts.control_center as cc
import scripts.serve_ui as su
from scripts.exchange import exchange
from src.config.settings import Settings
from src.core.contracts import Task
from src.manager.brain import BrainAction, BrainDecision
from src.manager.capabilities import IMAGE_TRANSFORM, MANAGER_CAPABILITIES, image_transform_intent, select_capability
from src.providers.media.image_edits import (
    EditOperation,
    apply_edit_plan,
    edit_operations,
    parse_edit_instruction,
    register_edit_operation,
)
from src.providers.media.image_techniques import BUILT_IN_TECHNIQUES, ImageTechniqueError


def _photo(size=(300, 200)) -> Image.Image:
    image = Image.new("RGB", size, (70, 110, 170))
    draw = ImageDraw.Draw(image)
    draw.rectangle((20, 20, 120, 180), fill=(240, 200, 30))
    draw.ellipse((170, 50, 280, 160), fill=(30, 150, 70))
    return image


def _png(image) -> bytes:
    buffer = io.BytesIO()
    image.save(buffer, "PNG")
    return buffer.getvalue()


def _ops(text):
    plan = parse_edit_instruction(text, BUILT_IN_TECHNIQUES)
    return [(s.op, dict(s.params)) for s in plan.steps], plan.not_understood


class TestInstructionsBecomePlans:
    def test_a_compound_instruction_keeps_its_order_and_parameters(self):
        steps, unknown = _ops("Turn the picture 270 degrees counter-clockwise; crop it to 4:3 and then add a 9px #336699 frame")
        assert unknown == ()
        assert steps == [("rotate", {"degrees": 270.0}), ("crop", {"mode": "ratio", "ratio": [4, 3]}),
                         ("border", {"width": 9, "colour": [51, 102, 153]})]

    @pytest.mark.parametrize("text, op, expected", [
        ("reduce the saturation of the image by 35%", "adjust", {"property": "saturation", "percent": -35.0}),
        ("make the photo slightly cooler", "temperature", {"percent": -12.0}),
        ("scale the image to 75%", "resize", {"scale": 0.75}),
        ("resize the picture to 640x480", "resize", {"width": 640, "height": 480}),
        ("keep only the bottom third of this photo", "crop", {"mode": "region", "side": "bottom", "fraction": 1 / 3}),
        ("posterize the image to 8 levels", "filter", {"filter": "posterize", "levels": 8}),
        ("flip this photo upside down", "flip", {"axis": "vertical"}),
    ])
    def test_each_operation_reads_its_own_numbers_and_words(self, text, op, expected):
        steps, unknown = _ops(text)
        assert unknown == () and steps == [(op, expected)]

    def test_named_techniques_are_steps_and_their_phrases_are_protected(self):
        steps, unknown = _ops("convert the photo to black and white, then remove the noise and invert it")
        assert unknown == ()
        assert [s[0] for s in steps] == ["technique", "technique", "filter"]
        assert steps[0][1] == {"technique": "grayscale"} and steps[1][1] == {"technique": "denoise"}

    def test_quoted_text_is_kept_exactly_including_punctuation_and_case(self):
        steps, _ = _ops('Write "Café, Ouvert!" in the bottom right corner of the image in yellow')
        assert steps[0][1]["text"] == "Café, Ouvert!" and steps[0][1]["vertical"] == "bottom"
        assert steps[0][1]["horizontal"] == "right" and steps[0][1]["colour"] == [255, 255, 0]

    def test_what_is_not_understood_is_reported_never_dropped(self):
        steps, unknown = _ops("crop the photo to a square and make it look like a pencil sketch")
        assert [s[0] for s in steps] == ["crop"] and unknown == ("make it look like a pencil sketch",)


class TestPlansAreAppliedAndVerified:
    def test_every_step_is_measured_and_the_result_is_real(self):
        plan = parse_edit_instruction("rotate the image 90 degrees clockwise, then make it 40% brighter", BUILT_IN_TECHNIQUES)
        result = apply_edit_plan(_png(_photo()), plan.steps, BUILT_IN_TECHNIQUES)
        edited = Image.open(io.BytesIO(result.data)).convert("RGB")
        assert edited.size == (200, 300) and all(step["verified"] for step in result.applied)
        rotated = _photo().rotate(-90, expand=True)
        assert ImageStat.Stat(edited.convert("L")).mean[0] > ImageStat.Stat(rotated.convert("L")).mean[0] * 1.2

    def test_a_step_that_cannot_happen_stops_everything(self):
        plan = parse_edit_instruction("crop the image to 900x900", BUILT_IN_TECHNIQUES)
        with pytest.raises(ImageTechniqueError, match="larger than the image"):
            apply_edit_plan(_png(_photo()), plan.steps, BUILT_IN_TECHNIQUES)

    def test_verification_catches_an_edit_that_did_not_do_what_it_claimed(self):
        white = Image.new("RGB", (60, 60), (255, 255, 255))
        plan = parse_edit_instruction("make the image 50% brighter", BUILT_IN_TECHNIQUES)
        with pytest.raises(ImageTechniqueError, match="did not do what was asked"):
            apply_edit_plan(_png(white), plan.steps, BUILT_IN_TECHNIQUES)

    def test_a_new_operation_joins_the_vocabulary_with_no_parser_change(self):
        from PIL import ImageOps

        solarize = EditOperation(
            "solarize", "solarize [above <n>]",
            parse=lambda clause: {"threshold": 128} if "solarize" in clause else None,
            apply=lambda image, params: (ImageOps.solarize(image.convert("RGB"), params["threshold"]), "solarized"),
            verify=lambda before, after, params: None if before.tobytes() != after.tobytes() else "no change",
        )
        register_edit_operation(solarize)
        try:
            steps, unknown = _ops("solarize the photo and mirror it")
            assert [s[0] for s in steps] == ["solarize", "flip"] and unknown == ()
            assert "solarize" in [op.op_id for op in edit_operations()]
        finally:
            from src.providers.media import image_edits

            image_edits._REGISTERED.pop("solarize", None)


class TestRecognition:
    @pytest.mark.parametrize("text", [
        "Could you crop this picture to 16:9?", "Rotate my photo 45 degrees", "make this photo look like a watercolour",
        "Add a thin black border to the image",
    ])
    def test_edit_requests_reach_image_transform(self, text):
        assert image_transform_intent(Task(description=text))
        assert select_capability(Task(description=text), MANAGER_CAPABILITIES) is IMAGE_TRANSFORM

    @pytest.mark.parametrize("text", ["Is this photo too dark?", "What's in this image?", "Draw an image of a lighthouse at dusk"])
    def test_questions_and_generation_are_not_edits(self, text):
        assert not image_transform_intent(Task(description=text))


@pytest.fixture
def system(tmp_path, monkeypatch):
    for name, value in (
        ("DEFAULT_ACCOUNTS_PATH", tmp_path / "accounts.json"), ("DEFAULT_CONFIG_PATH", tmp_path / "config.json"),
        ("DEFAULT_LEDGER_PATH", tmp_path / "state.json"), ("DEFAULT_STAGING_DIR", tmp_path / "staging"),
        ("DEFAULT_BACKUPS_DIR", tmp_path / "backups"),
    ):
        monkeypatch.setattr(cc, name, value)
    return su.build_live_system(Settings.from_env(
        env={}, memory="ephemeral", profile_path="does-not-exist-USER.md",
        artifacts_dir=str(tmp_path / "artifacts"), media_asset_dir=str(tmp_path / "assets"),
        knowledge_dir=str(tmp_path / "knowledge"), documents_dir=str(tmp_path / "docs"),
    ))


def _share(system, image) -> str:
    status, body, _ = su.route_upload(system.manager.own_skills.name, "shot.png", "image/png",
                                      base64.b64encode(_png(image)).decode(), "conversation", system)
    assert status == 200, body
    return json.loads(body)["artifact_id"]


class TestThroughTheServedSystem:
    def test_an_instruction_produces_a_derived_image_and_the_source_is_untouched(self, system):
        source = _share(system, _photo())
        before = open(system.runtime.artifacts.get(source).latest.renditions["png"], "rb").read()
        turn = exchange(system.channel, system.conversation, "Flip this image horizontally and crop the top half")
        assert "Artifact created" in turn.outcome and "checked" in turn.outcome
        made = [a for a in system.runtime.artifacts.all() if a.artifact_id != source][0]
        assert made.latest.related_artifact_ids == (source,)
        assert Image.open(made.latest.renditions["png"]).size == (300, 100)
        assert json.loads(made.latest.metadata["edits"])[0]["op"] == "flip"
        assert open(system.runtime.artifacts.get(source).latest.renditions["png"], "rb").read() == before

    def test_without_a_brain_an_unreadable_edit_is_refused_and_nothing_is_made(self, system):
        _share(system, _photo())
        count = len(system.runtime.artifacts.all())
        turn = exchange(system.channel, system.conversation, "Make this photo look like a stained glass window")
        assert "don't know how to" in turn.outcome and "nothing was changed" in turn.outcome
        assert len(system.runtime.artifacts.all()) == count

    def test_a_brain_restates_an_instruction_in_the_vocabulary_and_it_is_validated(self, system):
        _share(system, _photo())
        asked = []

        def brain(task, notes):
            asked.append(notes)
            return BrainDecision(BrainAction.HANDLE_SELF, "desaturate the image ; add a 6px black border", source="scripted_brain")

        system.manager.own_skills._converse = brain
        turn = exchange(system.channel, system.conversation, "Give this photo a stark gallery look")
        assert "Artifact created" in turn.outcome and asked and "ONLY these forms" in asked[0][0]
        made = max(system.runtime.artifacts.all(), key=lambda a: a.latest.created_at)
        edited = Image.open(made.latest.renditions["png"]).convert("RGB")
        assert edited.size == (312, 212) and edited.getpixel((1, 1)) == (0, 0, 0)
        assert ImageStat.Stat(edited.crop((6, 6, 306, 206)).convert("HSV")).mean[1] < 1

    def test_a_brain_answer_outside_the_vocabulary_is_not_applied(self, system):
        _share(system, _photo())
        system.manager.own_skills._converse = lambda task, notes: BrainDecision(
            BrainAction.HANDLE_SELF, "apply a van gogh style transfer ; CANNOT: style transfer", source="scripted_brain")
        count = len(system.runtime.artifacts.all())
        turn = exchange(system.channel, system.conversation, "Make this picture look like Starry Night")
        assert "don't know how to" in turn.outcome and len(system.runtime.artifacts.all()) == count
