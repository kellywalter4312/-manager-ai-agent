"""Stage 10 - the multimodal seam: a representation of what kind of thing a
person brought into a conversation, not a processor for any of them.

The point of this file is almost entirely negative: proving IMAGE/VIDEO/FILE
can be *named* without this project claiming, anywhere, that a camera, a
video decoder or a file reader exists. There is no fake camera to test
because there is no camera code at all - only an enum and a value type.
"""

from __future__ import annotations

import ast
from pathlib import Path

import pytest

from src.channels.modality import ConversationInput, InputModality

REPO_ROOT = Path(__file__).resolve().parent.parent
MODALITY_SRC = REPO_ROOT / "src" / "channels" / "modality.py"


class TestInputModality:
    def test_every_documented_modality_exists(self):
        names = {m.value for m in InputModality}
        assert names == {"text", "audio", "image", "video", "file"}

    def test_is_a_plain_json_safe_value(self):
        assert InputModality.IMAGE.value == "image"
        assert isinstance(InputModality.IMAGE.value, str)


class TestConversationInput:
    def test_text_is_handled(self):
        assert ConversationInput(modality=InputModality.TEXT, text="hi").is_handled is True

    def test_audio_is_handled(self):
        """Handled via an existing seam - an STT provider turning it into
        text - not because this type itself transcribes anything."""
        assert ConversationInput(modality=InputModality.AUDIO).is_handled is True

    def test_image_is_not_yet_handled(self):
        assert ConversationInput(modality=InputModality.IMAGE).is_handled is False

    def test_video_is_not_yet_handled(self):
        assert ConversationInput(modality=InputModality.VIDEO).is_handled is False

    def test_file_is_not_yet_handled(self):
        assert ConversationInput(modality=InputModality.FILE).is_handled is False

    def test_media_is_opaque_and_never_interpreted(self):
        payload = ConversationInput(
            modality=InputModality.IMAGE, media={"path": "whatever.jpg", "note": "anything"}
        )
        assert payload.media == {"path": "whatever.jpg", "note": "anything"}

    def test_is_immutable(self):
        value = ConversationInput(modality=InputModality.TEXT, text="hi")
        with pytest.raises(Exception):
            value.text = "changed"

    def test_describe_names_handled_state_honestly(self):
        assert "not yet handled" in ConversationInput(modality=InputModality.VIDEO).describe()
        assert "not yet handled" not in ConversationInput(modality=InputModality.TEXT).describe()


class TestNoFakeMediaProcessingExists:
    """Items 26/27: no camera/video implementation is reported as real, and
    the existing text/voice pipeline is untouched by this module's mere
    existence."""

    def test_the_module_contains_no_media_processing(self):
        source = MODALITY_SRC.read_text(encoding="utf-8").lower()
        for forbidden in (
            "cv2", "opencv", "pillow", "pil.image", "cv.videocapture",
            "getusermedia", "mediarecorder", "ffmpeg",
        ):
            assert forbidden not in source, forbidden

    def test_the_module_is_not_imported_by_the_existing_channel_pipeline(self):
        """The text/voice pipeline must be provably unaffected - it does not
        even know this module exists yet."""
        for path in (
            REPO_ROOT / "src" / "channels" / "channel.py",
            REPO_ROOT / "src" / "channels" / "activation.py",
        ):
            tree = ast.parse(path.read_text(encoding="utf-8"))
            for node in ast.walk(tree):
                if isinstance(node, ast.ImportFrom) and node.module:
                    assert node.module != "src.channels.modality", path.name

    def test_the_module_imports_nothing_besides_the_standard_library(self):
        tree = ast.parse(MODALITY_SRC.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if isinstance(node, ast.ImportFrom) and node.module:
                assert "src" not in node.module, node.module
            if isinstance(node, ast.Import):
                for alias in node.names:
                    assert not alias.name.startswith("src"), alias.name
