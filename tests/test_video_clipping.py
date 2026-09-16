"""Video clipping - cutting real clips from an existing video, distinct
from media_production's own video *generation* (a new video from a
prompt) and *composition* (assembling produced image/audio scenes).

Every test that produces a clip shells out to a real, genuine FFmpeg
binary (via imageio-ffmpeg, no network) and decodes the real result
with probe_duration_seconds - never a description of what a clip would
be. The AI-guided path uses a scripted LLMProvider standing in for the
unfunded vendor account (the same established discipline every other
Brain-touching test in this project already follows), and is proven
never to fabricate boundaries when no real text exists to reason over.
"""

from __future__ import annotations

import io
import wave
from pathlib import Path

import pytest

from src.agents.tools.media_production import (
    MediaProductionTool,
    find_video_path,
    parse_clip_specs,
)
from src.core.contracts import Task
from src.manager.capabilities import CLIP_VIDEO, MANAGER_CAPABILITIES, MEDIA_PRODUCTION, select_capability
from src.providers.media.image import LocalTitleCardImageProvider, NullImageProvider
from src.providers.media.narration import NullNarrationProvider
from src.providers.media.video import FFmpegVideoComposer, NullVideoComposer, VideoScene, probe_duration_seconds
from src.providers.media.video_clipping import ClippingError, ClipSpec, FFmpegVideoClipper, NullVideoClipper


def _real_source_video(seconds: float = 6.0) -> bytes:
    """A real, genuine, playable video, built the identical way
    FFmpegVideoComposer's own tests already do - never a hand-written
    fake container."""
    image = LocalTitleCardImageProvider().generate("clipping test source")
    buf = io.BytesIO()
    with wave.open(buf, "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(16000)
        w.writeframes(b"\x00\x00" * int(16000 * seconds))
    scene = VideoScene(image=image.data, audio=buf.getvalue(), duration_seconds=seconds)
    return FFmpegVideoComposer().compose([scene]).data


@pytest.fixture(scope="module")
def source_video() -> bytes:
    return _real_source_video()


# ---------------------------------------------------------------------------
# ClipSpec
# ---------------------------------------------------------------------------


class TestClipSpec:
    def test_negative_start_is_refused(self):
        with pytest.raises(ClippingError):
            ClipSpec(start_seconds=-1, end_seconds=5)

    def test_end_before_start_is_refused(self):
        with pytest.raises(ClippingError):
            ClipSpec(start_seconds=5, end_seconds=2)

    def test_end_equal_to_start_is_refused(self):
        with pytest.raises(ClippingError):
            ClipSpec(start_seconds=5, end_seconds=5)

    def test_duration_is_computed_correctly(self):
        assert ClipSpec(start_seconds=1.5, end_seconds=4.0).duration_seconds == pytest.approx(2.5)


# ---------------------------------------------------------------------------
# NullVideoClipper - the honest default
# ---------------------------------------------------------------------------


class TestNullVideoClipper:
    def test_refuses_honestly_rather_than_fabricate(self):
        with pytest.raises(ClippingError, match="no video-clipping engine"):
            NullVideoClipper().clip(b"not a real video", ClipSpec(0, 1))


# ---------------------------------------------------------------------------
# FFmpegVideoClipper - real, live media processing
# ---------------------------------------------------------------------------


class TestFFmpegVideoClipperProducesRealOutput:
    def test_a_real_clip_is_produced_with_the_right_duration(self, source_video):
        clip = FFmpegVideoClipper().clip(source_video, ClipSpec(start_seconds=1.0, end_seconds=3.5))
        assert clip.data
        assert clip.format == "mp4"
        real_duration = probe_duration_seconds(clip.data)
        assert real_duration == pytest.approx(2.5, abs=0.3)

    def test_the_clip_is_genuinely_shorter_than_the_source(self, source_video):
        clip = FFmpegVideoClipper().clip(source_video, ClipSpec(start_seconds=1.0, end_seconds=2.0))
        assert len(clip.data) < len(source_video)

    def test_the_clip_is_a_distinct_file_not_the_source_returned_unchanged(self, source_video):
        clip = FFmpegVideoClipper().clip(source_video, ClipSpec(start_seconds=0.0, end_seconds=1.0))
        assert clip.data != source_video

    def test_a_range_past_the_real_source_duration_is_refused_honestly(self, source_video):
        real_duration = probe_duration_seconds(source_video)
        with pytest.raises(ClippingError, match="extends past"):
            FFmpegVideoClipper().clip(
                source_video, ClipSpec(start_seconds=0.0, end_seconds=real_duration + 30)
            )

    def test_multiple_clips_from_one_source_are_each_real_and_distinct(self, source_video):
        specs = [ClipSpec(0.0, 1.5), ClipSpec(2.0, 4.0)]
        clipped = FFmpegVideoClipper().clip_many(source_video, specs)
        assert len(clipped) == 2
        assert clipped[0].video.data != clipped[1].video.data
        assert clipped[0].spec is specs[0]
        assert clipped[1].spec is specs[1]

    def test_clip_many_requires_at_least_one_spec(self, source_video):
        with pytest.raises(ClippingError):
            FFmpegVideoClipper().clip_many(source_video, [])


# ---------------------------------------------------------------------------
# Pure extraction functions
# ---------------------------------------------------------------------------


class TestParsing:
    def test_find_video_path_recognises_common_containers(self):
        for ext in ("mp4", "mov", "mkv", "webm", "avi"):
            assert find_video_path(f"clip data/source.{ext} from 1:00-2:00") == f"data/source.{ext}"

    def test_find_video_path_returns_none_when_absent(self):
        assert find_video_path("clip the video from 1:00-2:00") is None

    def test_parse_clip_specs_handles_mmss_and_hmmss(self):
        specs = parse_clip_specs("clip a.mp4 from 1:02:10-1:02:40 and 0:05-0:10")
        assert specs == [
            ClipSpec(start_seconds=3730.0, end_seconds=3760.0),
            ClipSpec(start_seconds=5.0, end_seconds=10.0),
        ]

    def test_parse_clip_specs_handles_bare_seconds(self):
        assert parse_clip_specs("clip a.mp4 from 10.5-20") == [ClipSpec(10.5, 20.0)]

    def test_parse_clip_specs_returns_empty_when_no_range_present(self):
        assert parse_clip_specs("clip a.mp4, find the best parts") == []


# ---------------------------------------------------------------------------
# MediaProductionTool's own "clip" operation
# ---------------------------------------------------------------------------


class TestMediaProductionToolClipOperation:
    def _tool(self, tmp_path, clipper=None):
        return MediaProductionTool(
            NullNarrationProvider(), NullImageProvider(), NullVideoComposer(),
            clipper=clipper or FFmpegVideoClipper(),
            asset_dir=tmp_path / "assets",
        )

    def test_a_real_clip_is_written_to_disk(self, tmp_path, source_video):
        source_path = tmp_path / "source.mp4"
        source_path.write_bytes(source_video)
        tool = self._tool(tmp_path)

        result = tool.run(operation="clip", content=f"clip {source_path} from 0:01-0:03")
        assert "1 clip(s) produced" in result

        produced = list((tmp_path / "assets").glob("*.mp4"))
        assert len(produced) == 1
        assert produced[0].stat().st_size > 0

    def test_multiple_ranges_produce_multiple_real_files(self, tmp_path, source_video):
        source_path = tmp_path / "source.mp4"
        source_path.write_bytes(source_video)
        tool = self._tool(tmp_path)

        result = tool.run(operation="clip", content=f"clip {source_path} 0:00-0:01 and 0:02-0:04")
        assert "2 clip(s) produced" in result
        assert len(list((tmp_path / "assets").glob("*.mp4"))) == 2

    def test_missing_source_path_is_refused(self, tmp_path):
        tool = self._tool(tmp_path)
        with pytest.raises(ValueError, match="real source video path"):
            tool.run(operation="clip", content="clip this from 0:00-0:01")

    def test_nonexistent_source_file_is_refused(self, tmp_path):
        tool = self._tool(tmp_path)
        with pytest.raises(ValueError, match="does not exist"):
            tool.run(operation="clip", content=f"clip {tmp_path / 'ghost.mp4'} from 0:00-0:01")

    def test_missing_time_range_is_refused(self, tmp_path, source_video):
        source_path = tmp_path / "source.mp4"
        source_path.write_bytes(source_video)
        tool = self._tool(tmp_path)
        with pytest.raises(ValueError, match="explicit 'start-end'"):
            tool.run(operation="clip", content=f"clip {source_path}")

    def test_the_source_file_is_left_completely_intact(self, tmp_path, source_video):
        source_path = tmp_path / "source.mp4"
        source_path.write_bytes(source_video)
        tool = self._tool(tmp_path)
        tool.run(operation="clip", content=f"clip {source_path} from 0:00-0:01")
        assert source_path.read_bytes() == source_video

    def test_the_null_clipper_refuses_honestly_through_the_tool(self, tmp_path, source_video):
        source_path = tmp_path / "source.mp4"
        source_path.write_bytes(source_video)
        tool = self._tool(tmp_path, clipper=NullVideoClipper())
        with pytest.raises(ClippingError):
            tool.run(operation="clip", content=f"clip {source_path} from 0:00-0:01")

    def test_clip_is_a_recognised_operation_in_run_dispatch(self, tmp_path):
        tool = self._tool(tmp_path)
        with pytest.raises(ValueError, match="unknown media_production operation"):
            tool.run(operation="not-a-real-operation", content="x")
        # The error message itself names 'clip' as a valid choice.
        try:
            tool.run(operation="bogus", content="x")
        except ValueError as exc:
            assert "clip" in str(exc)


# ---------------------------------------------------------------------------
# clip_video capability - routing, Half Brain, AI-guided
# ---------------------------------------------------------------------------


def real_manager(*, asset_dir: Path, **overrides):
    """The real production build() path, with media_asset_dir *and*
    artifacts_dir isolated to a real, disposable tmp_path - never the
    real data/media/assets or data/artifacts directories
    tests/conftest.py's own guard() already refuses writes into.
    Ecosystem artifact/workspace checkpoint: MediaProductionTool now
    also registers a real Artifact for every real production, so
    artifacts_dir needs the identical isolation media_asset_dir already
    had."""
    import scripts.control_center as cc
    from src.config.settings import Settings

    runtime, manager, channel, conversation = cc.build(
        Settings.from_env(
            env={}, memory="ephemeral", media_asset_dir=str(asset_dir),
            artifacts_dir=str(asset_dir.parent / "artifacts"), **overrides,
        )
    )
    return runtime, manager, channel, conversation


class TestClipVideoRoutingAndCapabilityBoundaries:
    def test_clip_video_is_registered(self):
        assert "clip_video" in MANAGER_CAPABILITIES
        assert MANAGER_CAPABILITIES["clip_video"] is CLIP_VIDEO

    def test_deterministic_phrasing_routes_to_clip_video(self):
        chosen = select_capability(
            Task(description="clip this video source.mp4 from 1:20-1:45"), MANAGER_CAPABILITIES
        )
        assert chosen is CLIP_VIDEO

    def test_ai_guided_phrasing_with_a_path_routes_to_clip_video(self):
        chosen = select_capability(
            Task(description="clip the video source.mp4, find the best moments"), MANAGER_CAPABILITIES
        )
        assert chosen is CLIP_VIDEO

    def test_generation_phrasing_still_routes_to_media_production(self):
        chosen = select_capability(
            Task(description="generate a video of a futuristic city"), MANAGER_CAPABILITIES
        )
        assert chosen is MEDIA_PRODUCTION

    def test_an_incidental_mention_of_clip_does_not_claim_this_capability(self):
        chosen = select_capability(
            Task(description="that was quite a clip show on television last night"), MANAGER_CAPABILITIES
        )
        assert chosen is not CLIP_VIDEO

    def test_a_time_range_stated_before_the_object_still_routes_to_clip_video(self):
        """Closure campaign (2026-09-16): found live, not in a unit test - a
        genuine shared video and a genuinely natural request ("clip 0:01 to
        0:04 of that video") fell through to open_conversation because the
        old fixed phrase list only matched a clip-verb immediately followed
        by "this/the video/footage". The verb and the object are both
        present here, just in a different, equally natural order."""
        chosen = select_capability(
            Task(description="Clip 0:01 to 0:04 of that video."), MANAGER_CAPABILITIES
        )
        assert chosen is CLIP_VIDEO

    def test_an_unrelated_use_of_a_clip_verb_with_no_video_noun_does_not_claim_this_capability(self):
        """The order-free check still requires a video/footage noun - a
        clip-verb alone, even beside something that looks like a time
        range, must not claim this capability."""
        chosen = select_capability(
            Task(description="I'll pick you up from 2 to 3, can you clip the coupon for me?"),
            MANAGER_CAPABILITIES,
        )
        assert chosen is not CLIP_VIDEO

    def test_clip_video_is_not_scoreable(self):
        assert CLIP_VIDEO.scoreable is False


class TestClipVideoHalfBrainSafety:
    """Deterministic clipping must work with zero LLM configured - the
    identical Half-Brain-safe rule this project already holds Tasks to."""

    def test_explicit_timestamps_need_no_brain(self, tmp_path, source_video):
        source_path = tmp_path / "clip_source.mp4"
        source_path.write_bytes(source_video)

        runtime, manager, channel, conversation = real_manager(
            asset_dir=tmp_path / "assets", media="local"
        )
        assert manager.brain.__class__.__name__ == "NullBrain"

        result = manager.handle_task(
            Task(description=f"clip this video {source_path} from 0:01-0:03")
        )
        assert result.succeeded, result.error
        assert result.payload.get("handled_by") == "clip_video"
        assert list((tmp_path / "assets").glob("*.mp4"))

    def test_missing_video_reference_is_refused_honestly(self, tmp_path):
        """An explicit time range is real evidence clip_video_intent
        correctly claims on its own (see that predicate's own docstring) -
        genuinely reaching clip_video() itself, which then finds no
        actual video path referenced anywhere in the request and refuses
        honestly rather than guessing one."""
        runtime, manager, channel, conversation = real_manager(
            asset_dir=tmp_path / "assets", media="local"
        )
        result = manager.handle_task(Task(description="clip this video from 1:00-2:00"))
        assert not result.succeeded
        assert "which video" in result.error.lower()

    def test_vague_clip_talk_with_no_evidence_does_not_falsely_claim_this_capability(self, tmp_path):
        """The other half of the same discipline: text that merely
        mentions clipping, with neither a real path nor an explicit
        range, is not this capability's request to answer - the
        identical "phrase alone is not enough" rule record_this/
        create_task already hold themselves to."""
        chosen = select_capability(
            Task(description="clip this video, find the best moments"), MANAGER_CAPABILITIES
        )
        assert chosen is not CLIP_VIDEO

    def test_ai_guided_without_a_transcript_is_refused_not_guessed(self, tmp_path, source_video):
        """Video-understanding checkpoint: this no longer refuses
        immediately. clip_video now genuinely attempts to derive real
        evidence from the video's own actual audio first (see that
        function's own docstring, step 3) - real, local FFmpeg
        extraction (which succeeds, since source_video has a genuine
        audio track) followed by transcription, which fails honestly
        here because MediaTools.transcriber defaults to
        NullTranscriptionProvider. Still refused, never guessed - only
        the reason now names the real, deeper attempt that was made."""
        source_path = tmp_path / "clip_source.mp4"
        source_path.write_bytes(source_video)
        runtime, manager, channel, conversation = real_manager(
            asset_dir=tmp_path / "assets", media="local"
        )
        result = manager.handle_task(
            Task(description=f"clip the video {source_path}, find the best moments")
        )
        assert not result.succeeded
        assert "understanding its actual content" in result.error
        assert "speech-transcription provider" in result.error


class TestClipVideoAIGuidedWithARealTranscript:
    def test_the_brain_proposes_boundaries_from_given_text_and_a_real_clip_is_produced(
        self, tmp_path, source_video
    ):
        from src.manager.llm_brain import LLMManagerBrain
        from src.providers.base import Completion, LLMProvider

        class ScriptedProvider(LLMProvider):
            def __init__(self):
                self.prompts = []

            @property
            def name(self):
                return "scripted"

            def complete(self, prompt, *, system=None):
                self.prompts.append(prompt)
                return Completion(text="0:01-0:03", provider="scripted")

        source_path = tmp_path / "clip_source.mp4"
        source_path.write_bytes(source_video)

        runtime, manager, channel, conversation = real_manager(
            asset_dir=tmp_path / "assets", media="local"
        )
        provider = ScriptedProvider()
        manager.brain = LLMManagerBrain(provider, personality=runtime.identity.personality)
        manager.own_skills._converse = manager.brain.converse

        transcript = (
            "Transcript: the host opens with a greeting, the crowd erupts into "
            "laughter moments later at the first real joke, and the segment "
            "closes on applause well before the clip's own short runtime ends."
        )
        result = manager.handle_task(
            Task(description=f"clip the video {source_path} - find the funniest moment. {transcript}")
        )
        assert result.succeeded, result.error
        assert provider.prompts, "the Brain must genuinely have been consulted"
        assert list((tmp_path / "assets").glob("*.mp4"))

    def test_a_brain_answer_with_no_parseable_range_is_an_honest_failure(self, tmp_path, source_video):
        from src.manager.llm_brain import LLMManagerBrain
        from src.providers.base import Completion, LLMProvider

        class UselessProvider(LLMProvider):
            @property
            def name(self):
                return "useless"

            def complete(self, prompt, *, system=None):
                return Completion(text="I'm not sure which part is funniest.", provider="useless")

        source_path = tmp_path / "clip_source.mp4"
        source_path.write_bytes(source_video)

        runtime, manager, channel, conversation = real_manager(
            asset_dir=tmp_path / "assets", media="local"
        )
        manager.brain = LLMManagerBrain(UselessProvider(), personality=runtime.identity.personality)
        manager.own_skills._converse = manager.brain.converse

        transcript = (
            "Transcript: a long, meandering conversation with no clearly funny "
            "or notable moment anywhere across its entire deliberately vague runtime."
        )
        result = manager.handle_task(
            Task(description=f"clip the video {source_path} - find the funniest moment. {transcript}")
        )
        assert not result.succeeded
        assert "couldn't settle on real clip boundaries" in result.error


# ---------------------------------------------------------------------------
# Provider wiring regression: MediaTools.clipper
# ---------------------------------------------------------------------------


class TestMediaToolsClipperWiring:
    def test_null_media_backend_gets_the_honest_null_clipper(self):
        from src.providers.media.factory import build_media
        from src.config.settings import Settings

        tools = build_media(Settings.from_env(env={}, media="null"))
        assert isinstance(tools.clipper, NullVideoClipper)

    def test_local_media_backend_gets_the_real_ffmpeg_clipper(self):
        from src.providers.media.factory import build_media
        from src.config.settings import Settings

        tools = build_media(Settings.from_env(env={}, media="local"))
        assert isinstance(tools.clipper, FFmpegVideoClipper)

    def test_ai_image_media_backend_also_gets_the_real_ffmpeg_clipper(self):
        from src.providers.media.factory import build_media
        from src.config.settings import Settings

        tools = build_media(Settings.from_env(env={}, media="ai_image"))
        assert isinstance(tools.clipper, FFmpegVideoClipper)

    def test_the_real_production_build_wires_the_real_clipper_through(self):
        import scripts.control_center as cc
        from src.config.settings import Settings

        runtime, manager, channel, conversation = cc.build(
            Settings.from_env(env={}, memory="ephemeral", media="local")
        )
        assert isinstance(runtime.media.clipper, FFmpegVideoClipper)
