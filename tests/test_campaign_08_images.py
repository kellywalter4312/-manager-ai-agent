"""Pre-Stage-9 Campaign 8 - image generation on its own governed axis, and
honest evidence about whether an image was edited or generated.

Real isolated systems through the production composition; the Control
Center's stores redirected to temp files. Real images are written with Pillow
(with and without an edited region and an editor's Software tag, and a PNG
carrying generation settings) - never a mocked examination. No image is ever
requested from a vendor.
"""

from __future__ import annotations

import base64
import io

import pytest
from PIL import Image, ImageDraw, PngImagePlugin

import scripts.control_center as cc
import scripts.serve_ui as su
from scripts.exchange import exchange
from src.config.settings import Settings
from src.core.contracts import Task
from src.manager.capabilities import image_authenticity_intent
from src.providers.media.factory import available_image_generation_backends, build_media
from src.providers.media.image import LocalTitleCardImageProvider, NullImageProvider, PollinationsImageProvider
from src.providers.media.image_forensics import ImageForensicsError, examine_image


@pytest.fixture
def isolated(tmp_path, monkeypatch):
    for name, leaf in (("DEFAULT_ACCOUNTS_PATH", "accounts.json"), ("DEFAULT_CONFIG_PATH", "config.json"),
                       ("DEFAULT_LEDGER_PATH", "state.json"), ("DEFAULT_STAGING_DIR", "staging"),
                       ("DEFAULT_BACKUPS_DIR", "backups")):
        monkeypatch.setattr(cc, name, tmp_path / leaf)
    return tmp_path


def _settings(tmp_path, **overrides):
    return Settings.from_env(
        env={}, memory="ephemeral", profile_path="does-not-exist-USER.md",
        artifacts_dir=str(tmp_path / "artifacts"), media_asset_dir=str(tmp_path / "assets"),
        knowledge_dir=str(tmp_path / "knowledge"), documents_dir=str(tmp_path / "docs"), **overrides,
    )


def _camera_jpeg() -> bytes:
    image = Image.new("RGB", (640, 400), (120, 160, 200))
    draw = ImageDraw.Draw(image)
    for x in range(0, 640, 20):
        draw.line([(x, 0), (x, 400)], fill=(100 + x % 80, 140, 180))
    exif = Image.Exif()
    exif[0x010F], exif[0x0110], exif[0x0132] = "Canon", "Canon EOS 80D", "2026:09:01 10:00:00"
    out = io.BytesIO()
    image.save(out, "JPEG", quality=90, exif=exif)
    return out.getvalue()


def _edited_jpeg() -> bytes:
    image = Image.open(io.BytesIO(_camera_jpeg())).convert("RGB")
    ImageDraw.Draw(image).rectangle([200, 120, 420, 260], fill=(250, 40, 40))
    exif = Image.Exif()
    exif[0x010F], exif[0x0131] = "Canon", "Adobe Photoshop 25.0"
    out = io.BytesIO()
    image.save(out, "JPEG", quality=95, exif=exif)
    return out.getvalue()


def _generated_png() -> bytes:
    info = PngImagePlugin.PngInfo()
    info.add_text("parameters", "a lighthouse at dawn, Steps: 30, Sampler: Euler a")
    out = io.BytesIO()
    Image.new("RGB", (256, 256), (30, 60, 90)).save(out, "PNG", pnginfo=info)
    return out.getvalue()


class TestImageGenerationIsItsOwnAxis:
    def test_the_default_keeps_the_media_backends_own_illustrator(self, tmp_path):
        tools = build_media(_settings(tmp_path, media="local"))
        assert isinstance(tools.illustrator, LocalTitleCardImageProvider)

    @pytest.mark.parametrize("backend, kind", [("null", NullImageProvider), ("local", LocalTitleCardImageProvider), ("pollinations", PollinationsImageProvider)])
    def test_a_chosen_backend_replaces_only_the_illustrator(self, tmp_path, backend, kind):
        base = build_media(_settings(tmp_path, media="local"))
        chosen = build_media(_settings(tmp_path, media="local", image_generation=backend))
        assert isinstance(chosen.illustrator, kind)
        assert type(chosen.narrator) is type(base.narrator) and type(chosen.composer) is type(base.composer)

    def test_an_unknown_backend_fails_loudly(self, tmp_path):
        with pytest.raises(ValueError, match="Unknown image-generation backend"):
            build_media(_settings(tmp_path, media="local", image_generation="bogus"))

    def test_the_control_center_lists_the_category(self, isolated):
        system = su.build_live_system(_settings(isolated))
        listing = cc.run_command(system.centre, "provider image_generation_provider", 100, system.channel, system.conversation)
        for backend in available_image_generation_backends():
            assert backend in listing

    def test_use_saves_a_validated_choice_that_every_start_honours(self, isolated):
        system = su.build_live_system(_settings(isolated))
        run = lambda command: cc.run_command(system.centre, command, 100, system.channel, system.conversation)
        assert run("provider image_generation_provider use local").startswith("Saved: 'image_generation_provider' now uses 'local'")
        assert run("provider image_generation_provider use bogus").startswith("no such backend 'bogus'")
        restarted = su.build_live_system(_settings(isolated))
        assert restarted.runtime.settings.image_generation == "local"

    def test_use_is_a_confirmed_write_on_the_served_control_center(self, isolated):
        needs, _ = cc.classify_administrative_write("provider image_generation_provider use local")
        assert needs


class TestImageEvidence:
    def test_camera_metadata_and_even_recompression(self, tmp_path):
        path = tmp_path / "camera.jpg"
        path.write_bytes(_camera_jpeg())
        evidence = examine_image(path)
        assert evidence.camera == "Canon EOS 80D" and evidence.captured_at.startswith("2026:09:01")
        assert not evidence.editors_named and not evidence.uneven_recompression

    def test_an_editor_trace_and_an_uneven_region(self, tmp_path):
        path = tmp_path / "edited.jpg"
        path.write_bytes(_edited_jpeg())
        evidence = examine_image(path)
        assert evidence.software == "Adobe Photoshop 25.0" and "photoshop" in evidence.editors_named
        assert evidence.uneven_recompression

    def test_generation_settings_are_reported(self, tmp_path):
        path = tmp_path / "generated.png"
        path.write_bytes(_generated_png())
        evidence = examine_image(path)
        assert evidence.generation_parameters and "Generator traces are recorded" in evidence.describe()
        assert "applies to JPEG images" in evidence.describe()

    def test_every_description_refuses_a_verdict(self, tmp_path):
        path = tmp_path / "edited.jpg"
        path.write_bytes(_edited_jpeg())
        text = examine_image(path).describe()
        assert "evidence, not a verdict" in text and "can't tell you for certain" in text

    def test_a_file_that_is_not_an_image_is_an_honest_error(self, tmp_path):
        path = tmp_path / "broken.jpg"
        path.write_bytes(b"not an image")
        with pytest.raises(ImageForensicsError):
            examine_image(path)


class TestAskingInConversation:
    @pytest.mark.parametrize("text", [
        "was this photo edited?", "is this picture fake?", "check if this image is AI generated",
        "has this screenshot been photoshopped", "is it a real or fake photo?",
    ])
    def test_authenticity_questions_are_recognised(self, text):
        assert image_authenticity_intent(Task(description=text))

    @pytest.mark.parametrize("text", ["I edited the essay yesterday", "what's in this image?", "was this document edited?"])
    def test_other_requests_are_not(self, text):
        assert not image_authenticity_intent(Task(description=text))

    def test_a_shared_image_is_examined_and_the_answer_is_evidence(self, isolated):
        system = su.build_live_system(_settings(isolated))
        agent = system.manager.own_skills.name
        status, _body, _ = su.route_upload(agent, "holiday.jpg", "image/jpeg", base64.b64encode(_edited_jpeg()).decode(), "conversation", system)
        assert status == 200
        turn = exchange(system.channel, system.conversation, "was this photo edited?", addressed=agent)
        assert turn.capability == "image_authenticity" and turn.succeeded, turn.outcome
        assert "Adobe Photoshop 25.0" in turn.outcome and "evidence, not a verdict" in turn.outcome

    def test_with_nothing_shared_it_asks_which_image(self, isolated):
        system = su.build_live_system(_settings(isolated))
        turn = exchange(system.channel, system.conversation, "was this photo edited?", addressed=system.manager.own_skills.name)
        assert not turn.succeeded and "Which image?" in turn.outcome
