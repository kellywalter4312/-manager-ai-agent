"""Image intelligence beyond description - transformation, enhancement,
reconstruction and comparison - through one open, governed technique registry.

Proved with real pixels, never placeholders:

    a real image, shared through POST /upload
      -> a named technique ("make this black and white", "deblur this photo")
      -> a new, derived image artifact (the original untouched)
      -> reconstruction that measurably recovers a blurred image (lower error)
    two real images -> comparison by measurement (identical / same / changed / different)
    Ecosystem Evolution -> a brand-new technique composed with no code, a
      technique withdrawn and restored, each live on the very next request

All in Half Brain: no model is consulted anywhere.
"""

from __future__ import annotations

import base64
import io
import json
from pathlib import Path

import numpy as np
import pytest
from PIL import Image, ImageDraw, ImageFilter

import scripts.control_center as cc
import scripts.serve_ui as su
from scripts.exchange import exchange
from src.config.settings import Settings
from src.core.contracts import Task
from src.manager.capabilities import MANAGER_CAPABILITIES, select_capability
from src.providers.media.image_techniques import (
    BUILT_IN_TECHNIQUES,
    ImageTechniqueError,
    compare_images,
    image_techniques,
    technique_for_request,
    validate_technique_value,
)
from src.services.evolution.governed_config import load_governed_config


def _png(image) -> bytes:
    buffer = io.BytesIO()
    image.save(buffer, "PNG")
    return buffer.getvalue()


def _scene(color="red", square=False) -> "Image.Image":
    image = Image.new("RGB", (320, 240), (30, 80, 190))
    draw = ImageDraw.Draw(image)
    box = (100, 60, 220, 180)
    (draw.rectangle if square else draw.ellipse)(box, fill=color)
    for x in range(0, 320, 16):
        draw.line((x, 0, x, 239), fill=(240, 240, 240), width=1)
    return image


@pytest.fixture
def isolated(tmp_path, monkeypatch):
    for name, leaf in (("DEFAULT_ACCOUNTS_PATH", "accounts.json"), ("DEFAULT_CONFIG_PATH", "config.json"),
                       ("DEFAULT_LEDGER_PATH", "state.json"), ("DEFAULT_STAGING_DIR", "staging"),
                       ("DEFAULT_BACKUPS_DIR", "backups")):
        monkeypatch.setattr(cc, name, tmp_path / leaf)
    return tmp_path


def _system(tmp_path):
    return su.build_live_system(Settings.from_env(
        env={}, memory="ephemeral", profile_path="does-not-exist-USER.md",
        artifacts_dir=str(tmp_path / "artifacts"), media_asset_dir=str(tmp_path / "assets"),
        knowledge_dir=str(tmp_path / "knowledge"), documents_dir=str(tmp_path / "docs"),
    ))


def _upload(system, data: bytes, name: str) -> dict:
    status, body, _ = su.route_upload(
        system.manager.own_skills.name, name, "image/png", base64.b64encode(data).decode(), "conversation", system,
    )
    assert status == 200, body
    return json.loads(body)


def _say(system, text: str):
    return exchange(system.channel, system.conversation, text)


def _command(system, text: str) -> str:
    return cc.run_command(system.centre, text, 100, system.channel, system.conversation)


class TestTechniquesAreDataNotSubsystems:
    def test_every_built_in_technique_turns_real_pixels_into_real_pixels(self):
        source = _png(_scene())
        for technique in BUILT_IN_TECHNIQUES:
            output = technique.apply(source)
            decoded = Image.open(io.BytesIO(output.data))
            assert decoded.size == (output.width, output.height)
            assert output.detail.strip()

    def test_the_longest_declared_phrase_decides(self):
        techniques = image_techniques({})
        assert technique_for_request("remove the blur from this photo", techniques).technique_id == "deblur"
        assert technique_for_request("make this picture black and white", techniques).technique_id == "grayscale"
        assert technique_for_request("sharpen this image", techniques).technique_id == "sharpen"
        assert technique_for_request("what a lovely picture", techniques) is None

    def test_a_file_that_is_not_an_image_is_refused_plainly(self):
        with pytest.raises(ImageTechniqueError):
            BUILT_IN_TECHNIQUES[0].apply(b"not an image")


class TestReconstructionIsMeasurable:
    @pytest.mark.parametrize("radius", [1.0, 1.6, 2.5])
    def test_deblurring_recovers_a_blurred_image(self, radius):
        sharp = _scene().convert("L")
        blurred = sharp.filter(ImageFilter.GaussianBlur(radius=radius))
        deblur = next(t for t in BUILT_IN_TECHNIQUES if t.technique_id == "deblur")
        restored = Image.open(io.BytesIO(deblur.apply(_png(blurred)).data)).convert("L")
        truth = np.asarray(sharp, dtype=float)
        before = np.abs(np.asarray(blurred, dtype=float) - truth).mean()
        after = np.abs(np.asarray(restored, dtype=float) - truth).mean()
        assert after < before, (before, after)


class TestComparisonIsMeasurement:
    def test_identical_resaved_changed_and_different(self):
        original = _png(_scene())
        assert compare_images(original, original).verdict == "the same file, byte for byte"
        resized = _png(_scene().resize((640, 480)))
        assert compare_images(original, resized).verdict.startswith("visually the same")
        edited = _scene()
        ImageDraw.Draw(edited).rectangle((10, 10, 60, 50), fill="yellow")
        assert compare_images(original, _png(edited)).verdict == "the same picture with part of it changed"
        other = Image.new("RGB", (320, 240), (240, 240, 20))
        ImageDraw.Draw(other).line((0, 0, 319, 239), fill="black", width=9)
        assert compare_images(original, _png(other)).verdict == "different pictures"


class TestRouting:
    @pytest.mark.parametrize("text, name", [
        ("Make this image black and white.", "image_transform"),
        ("Can you remove the blur from this picture?", "image_transform"),
        ("Sharpen this photo.", "image_transform"),
        ("Compare these two images.", "image_comparison"),
        ("Is this photo the same as the reference image?", "image_comparison"),
        ("Make me an image of a lighthouse at dusk.", "media_production"),
        ("Describe this image.", "image_analysis"),
    ])
    def test_requests_reach_the_capability_that_owns_them(self, text, name):
        chosen = select_capability(Task(description=text), MANAGER_CAPABILITIES)
        assert chosen is not None and chosen.name == name

    def test_a_document_is_never_image_work(self):
        chosen = select_capability(Task(description="Enhance my CV document."), MANAGER_CAPABILITIES)
        assert chosen is None or chosen.name != "image_transform"


class TestThroughTheRealServedSystem:
    def test_a_shared_photo_is_transformed_into_a_new_image_and_the_original_is_untouched(self, isolated):
        system = _system(isolated)
        source = _png(_scene())
        shared = _upload(system, source, "holiday.png")
        turn = _say(system, "Make this image black and white.")
        assert turn.succeeded, turn.outcome
        made = next(a for a in system.runtime.artifacts.all() if a.latest.metadata.get("technique") == "grayscale")
        assert made.latest.related_artifact_ids == (shared["artifact_id"],)
        assert Image.open(made.latest.renditions["png"]).mode == "L"
        original = system.runtime.artifacts.get(shared["artifact_id"])
        assert Path(next(iter(original.latest.renditions.values()))).read_bytes() == source
        # "this image" is now the derived one: a further step works on it.
        turn = _say(system, "Now sharpen this image.")
        assert turn.succeeded, turn.outcome
        sharpened = next(a for a in system.runtime.artifacts.all() if a.latest.metadata.get("technique") == "sharpen")
        assert sharpened.latest.related_artifact_ids == (made.artifact_id,)

    def test_two_shared_photos_are_compared(self, isolated):
        system = _system(isolated)
        _upload(system, _png(_scene()), "reference.png")
        edited = _scene()
        ImageDraw.Draw(edited).rectangle((10, 10, 60, 50), fill="yellow")
        _upload(system, _png(edited), "latest.png")
        turn = _say(system, "Is this photo the same as the reference image?")
        assert turn.succeeded, turn.outcome
        assert "part of it changed" in turn.outcome
        assert "reference.png" in turn.outcome and "latest.png" in turn.outcome

    def test_a_new_technique_joins_through_evolution_with_no_code_and_is_live(self, isolated):
        system = _system(isolated)
        _upload(system, _png(_scene().filter(ImageFilter.GaussianBlur(1.5))), "old_scan.png")
        assert not _say(system, "Restore this old photo please.").capability == "image_transform"
        proposed = _command(
            system,
            "ecosystem image_technique.restore propose pipeline:denoise,deblur,sharpen|Restore|restore;restore_this_old "
            "low restore_old_photos|one_request_for_the_whole_clean-up",
        )
        proposal_id = proposed.split("'")[1]
        applied = _command(system, f"ecosystem image_technique.restore run {proposal_id} yes")
        assert applied.startswith("OK"), applied
        assert "Live now" in applied
        turn = _say(system, "Restore this old photo please.")
        assert turn.succeeded, turn.outcome
        made = next(a for a in system.runtime.artifacts.all() if a.latest.metadata.get("technique") == "restore")
        assert "Deblur" in made.latest.title or "restore" in made.latest.title.lower()
        rolled = _command(system, f"ecosystem image_technique.restore rollback {proposal_id} yes")
        assert rolled.startswith("OK"), rolled
        assert load_governed_config(cc.DEFAULT_CONFIG_PATH).get("image_technique.restore") is None

    def test_a_technique_is_withdrawn_and_restored_through_evolution(self, isolated):
        system = _system(isolated)
        _upload(system, _png(_scene()), "photo.png")
        proposal = _command(system, "ecosystem image_technique.upscale propose disabled low not needed|less clutter").split("'")[1]
        assert _command(system, f"ecosystem image_technique.upscale run {proposal} yes").startswith("OK")
        turn = _say(system, "Upscale this photo.")
        assert not any(a.latest.metadata.get("technique") == "upscale" for a in system.runtime.artifacts.all())
        assert _command(system, f"ecosystem image_technique.upscale rollback {proposal} yes").startswith("OK")
        turn = _say(system, "Upscale this photo.")
        assert turn.succeeded, turn.outcome

    def test_an_invalid_technique_state_is_refused(self):
        assert validate_technique_value("x", "pipeline:nonexistent|X|x") is not None
        assert validate_technique_value("x", "turbo") is not None
        assert validate_technique_value("x", "disabled") is None
