"""Pre-Phase 1.6 - Creative Media Ecosystem Reality Check.

CHECK found zero pre-existing media generation/production capability
anywhere in this project: ``src/channels/modality.py``'s own docstring
states plainly "this module processes nothing... no video [decoder]",
and it exists only to name *incoming* media a person might send, never
to produce any. The only genuinely real, local, offline media
infrastructure already present was Kokoro text-to-speech (``data/
voices/kokoro/``, already used for Alfred/Arthur's spoken conversation
replies) - reused here, not duplicated, for a *different* purpose:
producing a durable narration *asset* rather than playing a live reply.

**What was built, and why not a new agent or a fixed workflow.** Three
provider-neutral asset families
(``src/providers/media/{narration,image,video}.py``), the identical
shape ``LLMProvider``/``SpeechAdapter`` already establish - abstract
contract, honest null default, one real local implementation - plus one
governed tool (``MediaProductionTool``) and one capability
(``media_production``) that recognises three atomic asks (narrate,
illustrate, compose) by shape, never by genre, platform or topic. No
``VideoAgent``, no per-genre workflow, no fixed "research then script
then voice then video" pipeline exists anywhere: *when* each operation
happens is decided entirely by ``ManagerBrain.assess_objective``'s own
existing, already-general step-by-step reasoning
(``src/manager/objective_loop.py``) - the identical mechanism that
already handles job-search objectives, learning sessions and project
decisions, now handling a creative one for the same reason it can
handle anything else: nothing in it is subject-aware.

**Two genuine defects this checkpoint's own first real test exposed and
fixed, immediately, not deferred.** (1) ``draw a comparison``/``draw a
conclusion`` - ordinary English idioms - falsely matched the
illustration shape merely because "draw" is a common verb; fixed by
requiring "draw" to be followed by an actual image-shaped object.
(2) A confidently-claimed, genuinely creative imperative
("create a short story", "write one scene of narration text") that
``general_assistance`` could not classify as research or coding fell
through to a bare roster listing instead of genuine reasoning - stale
behaviour from before that capability's own claims tightened; fixed to
reach ``open_conversation``'s real Brain reasoning instead, since
writing prose needs no external tool at all.

Every claim below distinguishes real local production-path verification
(genuinely produced, genuinely inspected files) from what would need a
funded AI image/video vendor this environment does not have.
"""

from __future__ import annotations

import re
import subprocess
import wave

import imageio_ffmpeg
import pytest

from src.agents.tools.media_production import MediaProductionTool
from src.agents.tools.toolset import ToolSet
from src.config.settings import Settings
from src.core.contracts import Task
from src.manager.capabilities import select_capability
from src.manager.llm_brain import LLMManagerBrain
from src.manager.objective_loop import ObjectiveStatus, pursue_objective
from src.providers.base import Completion, LLMProvider
from src.providers.media.factory import build_media
from src.providers.media.image import ImageError, LocalTitleCardImageProvider, NullImageProvider
from src.providers.media.narration import KokoroNarrationProvider, NarrationError, NullNarrationProvider
from src.providers.media.video import FFmpegVideoComposer, NullVideoComposer, VideoError, VideoScene
from scripts.control_center import build
from scripts.exchange import exchange

KOKORO_MODEL = "data/voices/kokoro/kokoro-v1.0.int8.onnx"
KOKORO_VOICES = "data/voices/kokoro/voices.npz"


def real_system(**overrides):
    return build(Settings.from_env(env={}, memory="ephemeral", **overrides))


def real_media_stack():
    return (
        KokoroNarrationProvider(KOKORO_MODEL, KOKORO_VOICES),
        LocalTitleCardImageProvider(),
        FFmpegVideoComposer(),
    )


def ffmpeg_streams(video_bytes: bytes) -> str:
    """Genuinely re-decode a produced video with the real ffmpeg binary
    and return its stderr, which names every stream it actually found -
    the same technique this checkpoint's own live verification used,
    now pinned as a repeatable test."""
    import tempfile
    from pathlib import Path

    ffmpeg = imageio_ffmpeg.get_ffmpeg_exe()
    with tempfile.TemporaryDirectory() as tmp:
        path = Path(tmp) / "check.mp4"
        path.write_bytes(video_bytes)
        result = subprocess.run(
            [ffmpeg, "-i", str(path), "-f", "null", "-"], capture_output=True, text=True, timeout=60
        )
        return result.stderr


# ---------------------------------------------------------------------------
# 1. Narration - real, local, offline TTS as a durable asset
# ---------------------------------------------------------------------------


class TestNarrationProvider:
    def test_null_is_honest_never_fabricates_silence(self):
        with pytest.raises(NarrationError):
            NullNarrationProvider().synthesize("hello", "am_eric")

    def test_kokoro_produces_real_audio_of_the_right_shape(self):
        asset = KokoroNarrationProvider(KOKORO_MODEL, KOKORO_VOICES).synthesize(
            "This is a genuine narration test.", "am_eric"
        )
        assert len(asset.audio) > 1000
        assert asset.sample_rate > 0
        assert asset.duration_seconds > 0
        # A real, complete WAV container - readable by the standard
        # library's own wave module, not merely "some bytes".
        import io

        with wave.open(io.BytesIO(asset.audio), "rb") as handle:
            assert handle.getnframes() > 0

    def test_empty_text_is_refused_not_silently_synthesised(self):
        with pytest.raises(NarrationError):
            KokoroNarrationProvider(KOKORO_MODEL, KOKORO_VOICES).synthesize("", "am_eric")

    def test_different_text_produces_genuinely_different_audio(self):
        provider = KokoroNarrationProvider(KOKORO_MODEL, KOKORO_VOICES)
        a = provider.synthesize("A short sentence.", "am_eric")
        b = provider.synthesize("A completely different, much longer sentence about something else entirely.", "am_eric")
        assert a.audio != b.audio
        assert a.duration_seconds != b.duration_seconds


# ---------------------------------------------------------------------------
# 2. Image - a real, local, honestly-labelled renderer
# ---------------------------------------------------------------------------


class TestImageProvider:
    def test_null_is_honest_never_fabricates_a_blank_image(self):
        with pytest.raises(ImageError):
            NullImageProvider().generate("x")

    def test_local_title_card_produces_a_real_png(self):
        asset = LocalTitleCardImageProvider().generate("A robot exploring a quiet junkyard at dusk.")
        assert asset.format == "png"
        assert asset.width > 0 and asset.height > 0
        assert asset.data[:8] == b"\x89PNG\r\n\x1a\n"  # the real PNG magic bytes

    def test_empty_prompt_is_refused(self):
        with pytest.raises(ImageError):
            LocalTitleCardImageProvider().generate("   ")

    def test_different_text_produces_genuinely_different_images(self):
        provider = LocalTitleCardImageProvider()
        a = provider.generate("A quiet forest at dawn.")
        b = provider.generate("A bustling city market at noon.")
        assert a.data != b.data


# ---------------------------------------------------------------------------
# 3. Video - genuine local composition, genuinely inspected
# ---------------------------------------------------------------------------


class TestVideoComposer:
    def test_null_is_honest_never_fabricates_a_video(self):
        with pytest.raises(VideoError):
            NullVideoComposer().compose([])

    def test_composing_with_no_scenes_is_refused(self):
        with pytest.raises(VideoError):
            FFmpegVideoComposer().compose([])

    def test_a_real_video_is_produced_and_independently_verified_playable(self):
        """The core practical-verification claim of this whole
        checkpoint: not 'the architecture supports video', but a real
        file, genuinely re-decoded by a real ffmpeg binary afterward,
        with both a real video stream and a real audio stream found."""
        narrator, illustrator, composer = real_media_stack()
        narration = narrator.synthesize("A short test narration for the video pipeline.", "am_eric")
        image = illustrator.generate("A simple test scene.")
        video = composer.compose(
            [VideoScene(image=image.data, audio=narration.audio, duration_seconds=narration.duration_seconds)]
        )

        assert video.format == "mp4"
        assert len(video.data) > 1000
        assert video.data[4:8] in (b"ftyp",)  # the real MP4 container signature

        streams = ffmpeg_streams(video.data)
        assert "Video: h264" in streams
        assert "Audio: aac" in streams
        assert "Duration:" in streams
        # Genuinely re-decoded end to end with no error, and both a real
        # video and a real audio stream were actually found - not a
        # renamed image, not an empty file, not a text file pretending
        # to be a video.
        assert "Invalid data" not in streams

    def test_multi_scene_composition_produces_the_correct_total_duration(self):
        narrator, illustrator, composer = real_media_stack()
        scenes = []
        for line in ("The first scene begins.", "Something happens in the second scene."):
            narration = narrator.synthesize(line, "am_eric")
            image = illustrator.generate(line)
            scenes.append(VideoScene(image=image.data, audio=narration.audio, duration_seconds=narration.duration_seconds))

        video = composer.compose(scenes)
        expected = sum(s.duration_seconds for s in scenes)
        assert abs(video.duration_seconds - expected) < 0.05

        streams = ffmpeg_streams(video.data)
        match = re.search(r"Duration: (\d+):(\d+):([\d.]+)", streams)
        assert match is not None
        actual_seconds = int(match.group(1)) * 3600 + int(match.group(2)) * 60 + float(match.group(3))
        assert abs(actual_seconds - expected) < 0.5


# ---------------------------------------------------------------------------
# 4. The governed tool boundary
# ---------------------------------------------------------------------------


class TestMediaProductionTool:
    def _toolset(self, tmp_path):
        narrator, illustrator, composer = real_media_stack()
        tool = MediaProductionTool(narrator, illustrator, composer, asset_dir=tmp_path)
        return ToolSet([tool])

    def test_narrate_produces_a_real_file_on_disk(self, tmp_path):
        toolset = self._toolset(tmp_path)
        result = toolset.invoke("media_production", {"operation": "narrate", "content": "A real narration test."})
        assert result.ok
        path_str = re.search(r"(\S+\.wav)", result.output).group(1)
        from pathlib import Path

        assert Path(path_str).exists()
        assert Path(path_str).stat().st_size > 0

    def test_illustrate_produces_a_real_file_on_disk(self, tmp_path):
        toolset = self._toolset(tmp_path)
        result = toolset.invoke("media_production", {"operation": "illustrate", "content": "A real image test."})
        assert result.ok
        path_str = re.search(r"(\S+\.png)", result.output).group(1)
        from pathlib import Path

        assert Path(path_str).exists()

    def test_compose_reads_real_paths_from_prior_output_and_produces_a_real_video(self, tmp_path):
        toolset = self._toolset(tmp_path)
        narration = toolset.invoke("media_production", {"operation": "narrate", "content": "Scene one narration."})
        image = toolset.invoke("media_production", {"operation": "illustrate", "content": "Scene one image."})
        composed = toolset.invoke(
            "media_production",
            {"operation": "compose", "content": f"compose a video using {image.output} and {narration.output}"},
        )
        assert composed.ok
        assert "Video produced" in composed.output

    def test_an_unknown_operation_is_rejected_honestly(self, tmp_path):
        toolset = self._toolset(tmp_path)
        result = toolset.invoke("media_production", {"operation": "translate", "content": "x"})
        assert not result.ok
        assert "unknown" in result.error.lower()

    def test_composing_with_no_referenceable_assets_fails_honestly_not_silently(self, tmp_path):
        toolset = self._toolset(tmp_path)
        result = toolset.invoke("media_production", {"operation": "compose", "content": "there is nothing to compose here"})
        assert not result.ok


# ---------------------------------------------------------------------------
# 5. Shape recognition - general, and the "draw" idiom regression
# ---------------------------------------------------------------------------


class TestMediaProductionShapeRecognition:
    @pytest.mark.parametrize(
        "text",
        [
            "narrate this: once upon a time there was a lighthouse keeper",
            "can you illustrate a lighthouse on a stormy cliff",
            "please compose a video from these scenes",
            "draw a picture of a lighthouse at night",
        ],
    )
    def test_genuine_production_asks_are_recognised(self, text):
        cap = select_capability(Task(description=text), None, participants=["Alfred"])
        assert cap.name == "media_production"

    @pytest.mark.parametrize(
        "text",
        [
            "what is a good narration style for a documentary?",
            "I love how this movie uses narration to build suspense",
            "draw your own conclusions from this data",
            "draw a comparison between these two approaches",
            "let us draw a blank on that one and move on",
        ],
    )
    def test_ordinary_conversation_mentioning_the_same_words_is_not_misrouted(self, text):
        """The exact regression this checkpoint's own first live test
        found and fixed: 'draw' is an ordinary English verb in several
        idioms with nothing to do with an image, and an anchored-verb
        shape check alone cannot tell them apart from a genuine
        illustration request without this additional, still-general
        (never per-idiom) requirement."""
        cap = select_capability(Task(description=text), None, participants=["Alfred"])
        assert cap.name != "media_production", text


# ---------------------------------------------------------------------------
# 6. general_assistance no longer dumps a roster for creative writing
# ---------------------------------------------------------------------------


class TestGeneralAssistanceReachesGenuineReasoningForCreativeWriting:
    """The second defect this checkpoint's own first live test found:
    a confidently-claimed, genuinely creative imperative ("create a
    short story", "write one scene of narration text") that
    _general_kind_for cannot classify as research or coding used to
    fall through to a bare roster listing - stale behaviour from
    before general_assistance_intent's own claim tightened. Writing
    prose needs no external tool at all, so this now reaches genuine
    reasoning instead."""

    def test_creative_writing_reaches_genuine_reasoning_not_a_roster_dump(self):
        runtime, manager, channel, conversation = real_system()

        class Provider(LLMProvider):
            @property
            def name(self):
                return "test"

            def complete(self, prompt, *, system=None):
                system = system or ""
                if "should_retain" in system:
                    return Completion(text='{"should_retain": false, "content": null}', provider="test")
                if "begin an interactive learning" in system:
                    return Completion(text='{"should_start": false, "topic": null}', provider="test")
                if "candidate values" in system:
                    return Completion(text='{"is_ambiguous": false}', provider="test")
                return Completion(text="Once there was a lighthouse keeper who could talk to gulls.", provider="test")

        brain = LLMManagerBrain(Provider(), personality=runtime.identity.personality)
        manager.brain = brain
        manager.own_skills._converse = brain.converse

        for text in (
            "Create a short story about a lighthouse keeper",
            "write one short scene of narration text for this story",
        ):
            turn = exchange(channel, conversation, text)
            assert turn.succeeded
            assert "orchestrator and coordinator" not in turn.outcome.lower()
            assert "specialists (" not in turn.outcome.lower()
            assert turn.outcome == "Once there was a lighthouse keeper who could talk to gulls."

    def test_the_protected_tool_gated_rule_is_still_completely_unchanged(self):
        """Requirement N (ARCHITECTURE.md): coding/research remain
        gated by tool, never by Brain - reconfirmed unaffected by this
        fix, which only changes the *unclassifiable* fourth case."""
        cap = select_capability(Task(description="write a small function"), None, participants=["Alfred"])
        assert cap.name == "general_assistance"
        cap2 = select_capability(Task(description="research Liverpool"), None, participants=["Alfred"])
        assert cap2.name == "general_assistance"


# ---------------------------------------------------------------------------
# 7. Full production wiring
# ---------------------------------------------------------------------------


class TestProductionWiring:
    def test_the_null_default_is_honest_and_unchanged_by_default(self):
        runtime, manager, channel, conversation = real_system()
        assert runtime.media.name == "null+null+null+null"
        assert "media_production" in manager.own_skills.tools.names()
        turn = exchange(channel, conversation, "narrate this: a short test line")
        assert not turn.succeeded
        assert "no narration engine is configured" in turn.outcome.lower()

    def test_the_local_backend_is_genuinely_wired_and_works_through_exchange(self, tmp_path):
        runtime, manager, channel, conversation = real_system(media="local", media_asset_dir=str(tmp_path))
        assert runtime.media.name == "kokoro+local-title-card+ffmpeg+null"
        turn = exchange(channel, conversation, "narrate this: a short test line for production wiring")
        assert turn.succeeded
        assert "Narration produced" in turn.outcome

    def test_an_unknown_media_backend_name_fails_fast(self):
        with pytest.raises(ValueError):
            build_media(Settings(media="nonexistent"))

    def test_changing_the_llm_provider_never_touches_the_media_backend(self, tmp_path):
        """Independence, Section 6: Alfred's LLM provider and the media
        stack are two entirely separate axes of configuration."""
        runtime, manager, channel, conversation = real_system(media="local", media_asset_dir=str(tmp_path))
        original_media = runtime.media
        for label in ("vendor-a", "vendor-b"):
            manager.brain = LLMManagerBrain(_FakeConverseProvider(label), personality=runtime.identity.personality)
            manager.own_skills._converse = manager.brain.converse
            assert runtime.media is original_media


class _FakeConverseProvider(LLMProvider):
    def __init__(self, label):
        self._label = label

    @property
    def name(self):
        return self._label

    def complete(self, prompt, *, system=None):
        return Completion(text="ok", provider=self._label)


# ---------------------------------------------------------------------------
# 8. The dynamic objective loop discovers and executes production steps
#    itself - no fixed pipeline anywhere in this file or in production code
# ---------------------------------------------------------------------------


class TestDynamicCreativeMediaObjective:
    def test_the_objective_loop_discovers_narrate_illustrate_compose_itself(self, tmp_path):
        """The central proof this checkpoint exists for: nothing in
        production code decides that a creative objective needs
        writing, then narration, then an image, then composition, in
        that order - every step below is chosen by the scripted Brain's
        own assess_objective judgement, reading real evidence (including
        real file paths it was never told in advance) exactly as it
        already does for every other kind of objective this project has
        proven general."""
        runtime, manager, channel, conversation = real_system(media="local", media_asset_dir=str(tmp_path))

        class Provider(LLMProvider):
            def __init__(self):
                self.n = 0

            @property
            def name(self):
                return "test"

            def complete(self, prompt, *, system=None):
                system = system or ""
                if "should_retain" in system:
                    return Completion(text='{"should_retain": false, "content": null}', provider="test")
                if "begin an interactive learning" in system:
                    return Completion(text='{"should_start": false, "topic": null}', provider="test")
                if "candidate values" in system:
                    return Completion(text='{"is_ambiguous": false}', provider="test")
                if "judging the progress of one objective" in system:
                    self.n += 1
                    if self.n == 1:
                        return Completion(
                            text='{"status":"continue","next_step":"write one short scene of narration text, one sentence"}',
                            provider="test",
                        )
                    if self.n == 2:
                        return Completion(
                            text='{"status":"continue","next_step":"narrate this scene text: A lighthouse keeper waved at a passing ship."}',
                            provider="test",
                        )
                    if self.n == 3:
                        return Completion(
                            text='{"status":"continue","next_step":"illustrate this scene: A lighthouse keeper waved at a passing ship."}',
                            provider="test",
                        )
                    if self.n == 4:
                        image_path = re.search(r"(\S+\.png)", prompt).group(1)
                        audio_path = re.search(r"(\S+\.wav)", prompt).group(1)
                        return Completion(
                            text=f'{{"status":"continue","next_step":"compose a video using {image_path} and {audio_path}"}}',
                            provider="test",
                        )
                    return Completion(text='{"status":"complete","final_answer":"Here is your short video."}', provider="test")
                remark = prompt.split("\n\nContext")[0].split("\n\nRecent")[0]
                if remark.lower().startswith("write one short scene"):
                    return Completion(text="A lighthouse keeper waved at a passing ship.", provider="test")
                return Completion(text="(a natural reply)", provider="test")

        brain = LLMManagerBrain(Provider(), personality=runtime.identity.personality)
        manager.brain = brain
        manager.own_skills._converse = brain.converse

        outcome = pursue_objective(manager, Task(description="Create a short story video about a lighthouse keeper"))

        assert outcome.status is ObjectiveStatus.COMPLETED
        assert len(outcome.steps) == 5

        operations_seen = [step.result.output.split(" ")[0] for step in outcome.steps[1:]]
        assert operations_seen == ["A", "Narration", "Image", "Video"]

        video_step = outcome.steps[-1]
        video_path = re.search(r"(\S+\.mp4)", video_step.result.output).group(1)
        from pathlib import Path

        video_bytes = Path(video_path).read_bytes()
        streams = ffmpeg_streams(video_bytes)
        assert "Video: h264" in streams
        assert "Audio: aac" in streams

    def test_a_second_unrelated_creative_objective_proves_generality(self, tmp_path):
        """Section 14's own requirement, reproduced with a second,
        genuinely unrelated topic through the identical mechanism -
        never a second, topic-specific code path."""
        runtime, manager, channel, conversation = real_system(media="local", media_asset_dir=str(tmp_path))

        class Provider(LLMProvider):
            def __init__(self):
                self.n = 0

            @property
            def name(self):
                return "test"

            def complete(self, prompt, *, system=None):
                system = system or ""
                if "should_retain" in system:
                    return Completion(text='{"should_retain": false, "content": null}', provider="test")
                if "begin an interactive learning" in system:
                    return Completion(text='{"should_start": false, "topic": null}', provider="test")
                if "candidate values" in system:
                    return Completion(text='{"is_ambiguous": false}', provider="test")
                if "judging the progress of one objective" in system:
                    self.n += 1
                    if self.n == 1:
                        return Completion(
                            text='{"status":"continue","next_step":"narrate this: The striker missed an open goal from two yards out, and the whole stadium groaned as one."}',
                            provider="test",
                        )
                    return Completion(text='{"status":"complete","final_answer":"Done."}', provider="test")
                return Completion(text="(a natural reply)", provider="test")

        brain = LLMManagerBrain(Provider(), personality=runtime.identity.personality)
        manager.brain = brain
        manager.own_skills._converse = brain.converse

        outcome = pursue_objective(
            manager, Task(description="Make a short Nigerian football comedy clip about a missed goal")
        )
        assert outcome.steps[1].result.succeeded
        assert "Narration produced" in outcome.steps[1].result.output
