"""Visual understanding is one capability over two shapes of medium.

**The gap this closes.** Before this pass, a *still* picture could be looked
at and a *moving* one could not - not by a different provider, but not at
all. `image_analysis` resolved only `("image",)` from the artifact store and
only `.png/.jpg/...` from a named path, and nothing anywhere turned a
recording into something a vision provider could read. The consequences were
found by routing every natural phrasing of the same ask through the real
`select_capability`:

    "what do you see in this video?"            -> open_conversation
    "what is happening in this video?"          -> live_information (!)
    "say what it is doing in the footage"       -> translate (!)
    "tell me what the dog is doing in this video" -> open_conversation

The last is the worst of them: with a Full Brain, ordinary conversation
answers *confidently about a recording it never looked at*.

**What was repaired, and what deliberately was not.** No new capability, no
new provider axis, no subsystem for any subject or container. One
generalized primitive (`sample_frames`) turns a moving medium into real
stills, and the identical vision path a still already went through reads
them. The medium is data: which shape a request is about comes from the
artifact store's own recorded kind, or from the file, never from the
request's wording; the nouns a person may use are one table
(`_VISUAL_NOUNS`), so a medium that does not exist yet is one entry.

Nothing in this file, or in what it exercises, knows what is *in* a picture.
The dog in the validation instance below is a subject, not a feature: the
same code path answers for a machine, a crowd, a weather front or anything
else, and the tests assert that only the *mechanism* behaved - never that a
particular thing was recognised.

Vision here is scripted, deliberately: one real look at one frame through
this machine's local vision model takes minutes, which a suite cannot spend.
The real model is exercised once, outside the suite, and recorded in the
campaign report. What is real in every test below is FFmpeg: every frame is
genuinely decoded out of a genuinely encoded file.
"""

from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

from src.core.contracts import Task
from src.manager import capabilities as C
from src.manager.brain import BrainAction, BrainDecision
from src.manager.capabilities import CapabilityRequest
from src.services.identity.identity import AgentIdentity
from src.providers.media.video import VideoError, resolve_ffmpeg, sample_frames


# ---------------------------------------------------------------------------
# Real media, made here - never a fixture checked into the tree
# ---------------------------------------------------------------------------


def _moving_file(path: Path, *, seconds: int = 4) -> bytes:
    """A genuinely encoded, genuinely *changing* video. FFmpeg's own test
    pattern is used because its content demonstrably differs frame to frame,
    which is what makes "the samples came from different moments" provable."""
    subprocess.run(
        [resolve_ffmpeg(), "-y", "-loglevel", "error", "-f", "lavfi",
         "-i", f"testsrc=s=320x180:d={seconds}:r=15", "-pix_fmt", "yuv420p", str(path)],
        check=True, timeout=120,
    )
    return path.read_bytes()


def _audio_only_file(path: Path) -> bytes:
    subprocess.run(
        [resolve_ffmpeg(), "-y", "-loglevel", "error", "-f", "lavfi",
         "-i", "sine=f=440:d=2", str(path)],
        check=True, timeout=120,
    )
    return path.read_bytes()


def _still_file(path: Path) -> bytes:
    subprocess.run(
        [resolve_ffmpeg(), "-y", "-loglevel", "error", "-f", "lavfi",
         "-i", "color=c=red:s=80x60:d=1", "-frames:v", "1", str(path)],
        check=True, timeout=120,
    )
    return path.read_bytes()


@pytest.fixture
def moving(tmp_path) -> Path:
    target = tmp_path / "moving.mp4"
    _moving_file(target)
    return target


# ---------------------------------------------------------------------------
# The primitive
# ---------------------------------------------------------------------------


class TestFramesAreRealAndComeFromDifferentMoments:
    def test_every_frame_is_a_real_png_at_a_rising_timestamp(self, moving):
        frames = sample_frames(moving.read_bytes(), count=4)
        assert len(frames) == 4
        assert [f.at_seconds for f in frames] == sorted(f.at_seconds for f in frames)
        assert len({f.at_seconds for f in frames}) == 4
        for frame in frames:
            assert frame.image_bytes.startswith(b"\x89PNG")
            assert frame.media_type == "image/png"

    def test_the_frames_are_genuinely_different_pictures(self, moving):
        """The whole point of looking across time - four decodes of the same
        instant would prove nothing about a recording that changes."""
        frames = sample_frames(moving.read_bytes(), count=4)
        assert len({f.image_bytes for f in frames}) == 4

    def test_one_look_is_taken_from_the_middle(self, moving):
        frame, = sample_frames(moving.read_bytes(), count=1)
        assert 1.0 < frame.at_seconds < 3.0

    def test_a_medium_with_no_visual_content_is_refused_never_invented(self, tmp_path):
        with pytest.raises(VideoError) as raised:
            sample_frames(_audio_only_file(tmp_path / "sound.wav"), count=2)
        assert "no visual content" in str(raised.value)

    def test_nothing_at_all_is_refused(self):
        with pytest.raises(VideoError):
            sample_frames(b"", count=1)

    def test_an_impossible_request_is_refused_at_the_boundary(self, moving):
        for bad in ({"count": 0}, {"width": 0}):
            with pytest.raises(ValueError):
                sample_frames(moving.read_bytes(), **bad)

    def test_a_sampled_frame_is_scaled_down_not_kept_full_size(self, tmp_path):
        """A frame is sampled to be looked at, never kept as a rendition."""
        big = tmp_path / "big.mp4"
        subprocess.run(
            [resolve_ffmpeg(), "-y", "-loglevel", "error", "-f", "lavfi",
             "-i", "testsrc=s=1920x1080:d=2:r=10", "-pix_fmt", "yuv420p", str(big)],
            check=True, timeout=120,
        )
        frame, = sample_frames(big.read_bytes(), count=1, width=320)
        # Read the PNG's own declared width out of its IHDR - never trusting
        # the filter string to have been applied.
        width = int.from_bytes(frame.image_bytes[16:20], "big")
        assert width == 320


# ---------------------------------------------------------------------------
# Routing: the same ask about a moving medium, in every natural order
# ---------------------------------------------------------------------------


class TestTheAskReachesVisualUnderstandingWhateverTheWordOrder:
    @pytest.mark.parametrize("text", [
        "what do you see in this video?",
        "describe this video",
        "what is happening in this video?",
        "what's going on in that clip?",
        "describe that footage",
        "tell me what is going on in the video I just shared",
        "tell me what the dog is doing in this video",
        "say what the dog is doing in the footage",
        "voice out what the dog is doing in this video",
        "what do you see in this animation?",
    ])
    def test_a_request_about_a_moving_medium_reaches_visual_understanding(self, text):
        assert C.select_capability(Task(description=text)).name == "image_analysis"

    @pytest.mark.parametrize("text,expected", [
        # Every neighbour that legitimately owns its own request keeps it.
        ("clip the video from 0:01 to 0:04", "clip_video"),
        ("transcribe this video", "transcribe_media"),
        ("transcribe this recording", "transcribe_media"),
        ("translate this video into English", "translate_media"),
        ("give me English subtitles for this video", "translate_media"),
        ("narrate this: once upon a time there was a lighthouse keeper", "media_production"),
        ("I need a voiceover for this scene, can you narrate it?", "media_production"),
        ("generate a video of a futuristic city", "media_production"),
        ("compose a video from these scenes", "media_production"),
        ("make me a picture of a harbour", "media_production"),
        # And the still-picture requests that always worked.
        ("look at this image", "image_analysis"),
        ("describe this picture", "image_analysis"),
    ])
    def test_no_neighbour_loses_its_own_request(self, text, expected):
        assert C.select_capability(Task(description=text)).name == expected

    def test_every_phrase_recognised_before_this_pass_is_still_recognised(self):
        """The marker table is generated now. Nothing it used to hold may
        have been dropped by generating it."""
        for phrase in (
            "look at this image", "look at the image", "what do you see in this image",
            "what is in this image", "what's in this image", "analyze this image",
            "describe this image", "look at this picture", "what's in this picture",
            "analyze this picture", "describe this picture",
        ):
            assert phrase in C.IMAGE_ANALYSIS_MARKERS

    def test_a_new_medium_word_needs_no_new_sentences(self):
        """The generalization itself: adding one noun to the table makes
        every ask shape work for it, with no source change anywhere else."""
        assert all(
            f"what do you see in this {noun}" in C.IMAGE_ANALYSIS_MARKERS
            for noun in C._VISUAL_NOUNS
        )


# ---------------------------------------------------------------------------
# The handler: one capability, two shapes of medium
# ---------------------------------------------------------------------------


class _Seen:
    """A vision provider's answers, scripted per call so "it looked more than
    once" is provable, and so nothing here depends on what is in a picture."""

    def __init__(self, answers):
        self.answers = list(answers)
        self.prompts: "list[str]" = []
        self.sizes: "list[int]" = []

    def __call__(self, image_bytes, prompt, **_):
        self.prompts.append(prompt)
        self.sizes.append(len(image_bytes))
        text = self.answers[min(len(self.prompts) - 1, len(self.answers) - 1)]
        return BrainDecision(action=BrainAction.REPORT, reason=text, confidence=0.9, source="scripted-vision")


IDENTITY = AgentIdentity(
    name="Alfred", role="Orchestrator", description="Coordinates work.",
    capabilities=frozenset({"vision", "image_analysis"}),
)


def _probe_request(description: str, *, analyze_image=None, assist=None):
    """A CapabilityRequest carrying only what this handler actually reads -
    the same hand-built shape `tests/test_manager_capabilities.py::a_request`
    already uses, so nothing here depends on a composition root."""
    return CapabilityRequest(
        task=Task(description=description),
        identity=IDENTITY,
        roster=lambda: (),
        own_name=IDENTITY.name,
        own_capabilities=tuple(sorted(IDENTITY.capabilities)),
        analyze_image=analyze_image,
        assist=assist,
    )


class TestAMovingMediumIsLookedAtAcrossTime:
    def test_several_moments_are_looked_at_and_each_is_reported_with_its_time(self, moving):
        vision = _Seen(["a shape on the left", "the shape in the middle", "the shape on the right"])
        result = C.image_analysis(_probe_request(f"describe {moving}", analyze_image=vision))
        assert result.succeeded, result.output
        assert len(vision.prompts) == C.MOVING_VISUAL_SAMPLE_COUNT
        assert result.payload["medium"] == "moving"
        assert result.payload["frames_looked_at"] == C.MOVING_VISUAL_SAMPLE_COUNT
        # Every look is attributed to the second it came from.
        for seconds in result.payload["sampled_at_seconds"]:
            assert f"At {seconds:g}s:" in result.output
        assert "a shape on the left" in result.output
        assert "the shape on the right" in result.output

    def test_the_account_says_it_is_only_the_moments_it_looked_at(self, moving):
        result = C.image_analysis(_probe_request(f"describe {moving}", analyze_image=_Seen(["something"])))
        assert C._SAMPLED_LOOK_NOTE in result.output

    def test_each_look_asks_the_persons_own_question(self, moving):
        vision = _Seen(["x"])
        C.image_analysis(_probe_request(f"what is happening in {moving}", analyze_image=vision))
        assert all("what is happening in" in prompt for prompt in vision.prompts)

    def test_a_still_picture_is_still_one_look_with_no_timestamps(self, tmp_path):
        still = tmp_path / "still.png"
        _still_file(still)
        vision = _Seen(["a red rectangle"])
        result = C.image_analysis(_probe_request(f"describe {still}", analyze_image=vision))
        assert result.succeeded, result.output
        assert len(vision.prompts) == 1
        assert "At " not in result.output
        assert result.output.strip() == "a red rectangle"
        assert (result.payload or {}).get("medium") is None


class TestSpeakingForWhatIsInAMediumStaysAnInterpretation:
    def test_asked_to_voice_a_subject_the_answer_says_it_is_a_reading(self, moving):
        result = C.image_analysis(_probe_request(
            f"voice out what the dog is doing in {moving}", analyze_image=_Seen(["a shape moves"]),
        ))
        assert result.succeeded, result.output
        assert C._INTERPRETIVE_NOTE in result.output
        # Never a claim that the subject expressed anything.
        assert "said" not in result.output.lower()

    def test_an_ordinary_description_carries_no_such_note(self, moving):
        result = C.image_analysis(_probe_request(
            f"describe {moving}", analyze_image=_Seen(["a shape moves"]),
        ))
        assert C._INTERPRETIVE_NOTE not in result.output

    @pytest.mark.parametrize("text", [
        "voice out what it is doing in this video",
        "say what he is doing in this video",
        "narrate this video",
        "tell me what they are saying in this video",
    ])
    def test_the_reading_is_recognised_however_it_is_phrased(self, text):
        assert C._SPEAK_FOR_SUBJECT.search(text) is not None


class TestHonestFailureNeverAFabricatedDescription:
    def test_a_medium_with_no_visual_content_is_reported_not_described(self, tmp_path):
        sound = tmp_path / "sound.mp4"
        subprocess.run(
            [resolve_ffmpeg(), "-y", "-loglevel", "error", "-f", "lavfi",
             "-i", "sine=f=440:d=2", "-c:a", "aac", str(sound)],
            check=True, timeout=120,
        )
        vision = _Seen(["this must never be reached"])
        result = C.image_analysis(_probe_request(f"describe {sound}", analyze_image=vision))
        assert not result.succeeded
        assert "couldn't get a look" in result.output or "couldn't get a look" in (result.error or "")
        assert vision.prompts == []

    def test_real_frames_but_no_vision_is_reported_as_exactly_that(self, moving):
        result = C.image_analysis(_probe_request(
            f"describe {moving}", analyze_image=lambda *a, **k: BrainDecision.no_view("no vision here"),
        ))
        assert not result.succeeded
        assert "no vision-capable reasoning is configured" in (result.output or "") + (result.error or "")

    def test_a_file_that_is_not_there_is_reported_as_missing(self, tmp_path):
        missing = tmp_path / "gone.mp4"
        result = C.image_analysis(_probe_request(f"describe {missing}", analyze_image=_Seen(["x"])))
        assert not result.succeeded
        assert "couldn't find" in (result.output or "") + (result.error or "")


class TestWhichShapeAMediumIsComesFromTheStoreNotTheWording:
    @pytest.mark.parametrize("described,expected", [
        ({"content_kind": "video"}, True),
        ({"content_kind": "video_recording"}, True),
        ({"type": "video"}, True),
        ({"content_kind": "image"}, False),
        ({"type": "diagram"}, False),
        # Nothing recorded: the file itself decides, never an assumption.
        ({}, True),
        (None, True),
    ])
    def test_the_recorded_kind_decides(self, described, expected):
        assert C._is_moving_medium(described, "somewhere/clip.mp4") is expected

    def test_a_still_path_is_never_treated_as_moving(self):
        assert C._is_moving_medium({}, "somewhere/photo.png") is False

    def test_a_recorded_kind_outranks_the_file_extension(self):
        """A file registered as a still is read as one even if its name
        looks otherwise - the store is the authority."""
        assert C._is_moving_medium({"content_kind": "image"}, "odd.mp4") is False


class TestTheVisionModelIsAskedAQuestionNotAFilePath:
    r"""Found in this pass's own live run: the small local vision model was
    handed "voice out what the subject is doing in this video:
    C:\...\subject.mp4" verbatim. The path is how this process found the
    file; it is noise to something being shown the picture itself."""

    def test_a_named_path_never_reaches_the_model(self):
        asked = C._vision_prompt(r"voice out what the subject is doing in this video: C:\tmp\subject.mp4")
        assert "subject.mp4" not in asked
        assert "C:" not in asked
        assert "voice out what the subject is doing in this video" == asked

    @pytest.mark.parametrize("text", [
        r"describe C:\x\still.png",
        r"C:\tmp\clip.mp4",
        "",
    ])
    def test_nothing_left_to_ask_falls_back_to_the_plain_question(self, text):
        assert C._vision_prompt(text) == C._DEFAULT_VISION_PROMPT

    def test_a_real_question_is_still_asked_as_the_person_asked_it(self):
        assert C._vision_prompt("what is the machine on the left doing?") == (
            "what is the machine on the left doing?"
        )

    def test_the_live_run_shape_is_covered_end_to_end(self, moving):
        """The same request shape the live run used, through the handler."""
        vision = _Seen(["a pale shape against a dark field"])
        result = C.image_analysis(_probe_request(
            f"voice out what the subject is doing in this video: {moving}", analyze_image=vision,
        ))
        assert result.succeeded, result.output
        assert all(str(moving) not in prompt for prompt in vision.prompts)
        assert C._INTERPRETIVE_NOTE in result.output
