"""The governed boundary between Alfred and real media asset production.

**The Brain decides what is needed and when. This only produces.** A
``Tool`` is handed its arguments and nothing else (see ``tool.py``'s own
docstring), so this class cannot decide that a creative objective needs
narration before an image, or a composed video before a generated one -
it has no view of the objective at all. That dynamic sequencing is the
objective loop's own job (``src/manager/objective_loop.py``), driven by
``ManagerBrain.assess_objective`` exactly as it already is for every
other kind of work; nothing about media production gets a second,
competing planning mechanism. This tool answers a single, narrow
question for one step at a time - "narrate this text", "illustrate this
description", "compose these assets into a video", or "generate an AI
video of this" - and does exactly that, through whichever real
``NarrationProvider``/``ImageProvider``/``VideoComposer``/
``VideoGenerationProvider`` it was constructed with.

Registered under the tool name ``media_production`` - one name for all
four operations, the same reason ``live_information`` is one tool
rather than one per data source: what actually backs each operation is
a replaceable provider, never something this class or its caller needs
to know the vendor of.

**``compose`` vs. ``generate_video``, and why both exist.** These are
genuinely different capabilities, not two names for one thing: ``compose``
locally assembles already-produced image and audio assets with FFmpeg
(no AI model involved in the assembly itself); ``generate_video`` asks a
real AI model to synthesise a new video clip from a prompt and,
optionally, a seed image (image-to-video). Which one - or whether
either - an objective actually needs is decided by the Brain's own
reasoning, never by this file; both remain available, and an honest
failure from one (say, no video-generation vendor configured) is
exactly the kind of real evidence the objective loop already knows how
to adapt to by trying the other.
"""

from __future__ import annotations

import re
from pathlib import Path

from src.agents.tools.tool import Tool, ToolSpec
from src.providers.media.image import ImageProvider
from src.providers.media.narration import NarrationProvider
from src.providers.media.video import FFmpegVideoComposer, VideoComposer, VideoScene
from src.providers.media.video_clipping import ClipSpec, NullVideoClipper, VideoClipper
from src.providers.media.video_generation import NullVideoGenerationProvider, VideoGenerationProvider

# Where real, produced assets are written so a later step (in the same or
# a later objective-loop iteration) can reference them by path - the
# identical "evidence carries forward as plain text" contract the whole
# objective loop already depends on (see objective_loop.py's own
# docstring), extended to file paths rather than prose findings.
DEFAULT_ASSET_DIR = Path("data") / "media" / "assets"

_ASSET_PATH_PATTERN = re.compile(r"\S+\.(?:png|wav)", re.IGNORECASE)
# Video clipping checkpoint: a source video referenced by path - the
# identical "a path in the text is the evidence" convention
# _ASSET_PATH_PATTERN already established, extended to the container
# formats a real source video plausibly arrives in.
_VIDEO_PATH_PATTERN = re.compile(r"\S+\.(?:mp4|mov|mkv|webm|avi)", re.IGNORECASE)
# One or more "<start>-<end>" ranges, each side either a bare number of
# seconds ("130.5") or an H:MM:SS/M:SS timecode ("1:02:10", "2:40") - the
# canonical, single format this tool's own content string is expected to
# already be in. Turning "from 2:10 to 2:40" or "the funny bit" into this
# shape is the capability layer's own job (natural-language extraction,
# and where needed, Brain reasoning over given text) - never this tool's,
# which only ever does the mechanical cut.
_TIME_RANGE_PATTERN = re.compile(
    r"((?:\d+:)?(?:\d+:)?\d+(?:\.\d+)?)\s*-\s*((?:\d+:)?(?:\d+:)?\d+(?:\.\d+)?)"
)


def _parse_timecode(text: str) -> float:
    """``"1:02:10"``/``"2:40"``/``"130.5"`` -> seconds. Raises
    ``ValueError`` on anything else, the same honest-refusal-over-guess
    rule every real parser in this project already holds itself to."""
    parts = text.strip().split(":")
    seconds = 0.0
    for part in parts:
        seconds = seconds * 60 + float(part)
    return seconds


def find_video_path(content: str) -> str | None:
    """The first referenced source-video path in ``content``, or None -
    shared, public evidence-check used by
    ``src.manager.capabilities.clip_video_intent`` to recognise its own
    request shape without duplicating this pattern in a second module."""
    match = _VIDEO_PATH_PATTERN.search(content)
    return match.group(0) if match else None


def parse_clip_specs(content: str) -> "list[ClipSpec]":
    """Every ``ClipSpec`` named in ``content``'s own ``start-end`` ranges,
    in the order they appear - shared by ``MediaProductionTool._clip``
    and, directly, by ``src.manager.capabilities.clip_video``, which
    needs the same parse to decide *whether* a request already names
    explicit boundaries before ever reaching this tool (see that
    capability's own docstring for why that distinction matters)."""
    return [
        ClipSpec(start_seconds=_parse_timecode(start), end_seconds=_parse_timecode(end))
        for start, end in _TIME_RANGE_PATTERN.findall(content)
    ]


class MediaProductionTool(Tool):
    """Wraps a narration provider, an image provider and a video
    composer behind the ordinary ``Tool`` contract - one tool, three
    operations, each a genuine, real, local production step."""

    def __init__(
        self,
        narrator: NarrationProvider,
        illustrator: ImageProvider,
        composer: VideoComposer,
        video_generator: VideoGenerationProvider | None = None,
        clipper: VideoClipper | None = None,
        *,
        voice_id: str = "am_eric",
        asset_dir: Path | None = None,
    ) -> None:
        self._narrator = narrator
        self._illustrator = illustrator
        self._composer = composer
        # Optional and additive: every existing caller that omits it
        # (constructed before the AI video-generation checkpoint added
        # this parameter) gets the identical honest NullVideoGenerationProvider
        # default every other Null* collaborator in this project already is.
        self._video_generator = video_generator or NullVideoGenerationProvider()
        # Video clipping checkpoint: the identical optional/additive
        # default, for the identical reason.
        self._clipper = clipper or NullVideoClipper()
        self._voice_id = voice_id
        self._asset_dir = asset_dir or DEFAULT_ASSET_DIR

    @property
    def spec(self) -> ToolSpec:
        return ToolSpec(
            name="media_production",
            description=(
                "Produces one real media asset per call - narrate text into "
                "audio, illustrate a description into an image, compose "
                "already-produced assets into a video, generate a real AI "
                "video directly, or cut real clips from an existing video - "
                "never a fabricated or placeholder file."
            ),
            schema={"operation": str, "content": str},
        )

    def run(self, **arguments: str) -> str:
        """Perform exactly one production operation and return a
        description of the real asset produced, including its file path -
        the evidence a later objective-loop step needs to reference it.

        Raises like ordinary Python on failure, the same contract every
        ``Tool`` lives under (``ToolSet.invoke`` converts it into an
        honest ``ToolResult.failed`` at its own boundary).
        """
        operation = arguments["operation"].strip().lower()
        content = arguments["content"]

        if operation == "narrate":
            return self._narrate(content)
        if operation == "illustrate":
            return self._illustrate(content)
        if operation == "compose":
            return self._compose(content)
        if operation == "generate_video":
            return self._generate_video(content)
        if operation == "clip":
            return self._clip(content)
        raise ValueError(
            f"unknown media_production operation {operation!r}; "
            "expected one of: narrate, illustrate, compose, generate_video, clip"
        )

    def _write(self, suffix: str, data: bytes) -> Path:
        self._asset_dir.mkdir(parents=True, exist_ok=True)
        # Content-addressed, so the identical text/prompt never produces
        # two different filenames across calls - a small, free form of
        # deduplication, not a cache with its own invalidation problem.
        import hashlib

        digest = hashlib.sha256(data).hexdigest()[:16]
        path = self._asset_dir / f"{digest}{suffix}"
        if not path.exists():
            path.write_bytes(data)
        return path

    def _narrate(self, text: str) -> str:
        text = text.strip()
        if not text:
            raise ValueError("narration text must not be empty")
        asset = self._narrator.synthesize(text, self._voice_id)
        path = self._write(".wav", asset.audio)
        return (
            f"Narration produced: {path.as_posix()} "
            f"({asset.duration_seconds:.2f}s, voice={asset.voice_id})"
        )

    def _illustrate(self, description: str) -> str:
        description = description.strip()
        if not description:
            raise ValueError("an image description must not be empty")
        asset = self._illustrator.generate(description)
        path = self._write(".png", asset.data)
        return f"Image produced: {path.as_posix()} ({asset.width}x{asset.height})"

    def _generate_video(self, content: str) -> str:
        """AI-generate a real video clip from ``content`` - a prompt,
        and, if the text references one, a previously-produced image
        used as a seed (image-to-video). Whether a seed image is
        referenced at all is entirely up to what the caller's own
        step text contains - this method never requires one and never
        invents a reason to look for one beyond what is actually there.
        """
        seed_paths = [Path(m) for m in _ASSET_PATH_PATTERN.findall(content) if m.lower().endswith(".png")]
        seed_image: bytes | None = None
        if seed_paths and seed_paths[0].exists():
            seed_image = seed_paths[0].read_bytes()

        prompt = content.strip()
        if not prompt and seed_image is None:
            raise ValueError("a prompt or a referenced seed image is required to generate a video")

        asset = self._video_generator.generate(prompt, seed_image=seed_image)
        path = self._write(".mp4", asset.data)
        seeded = f", seeded from {seed_paths[0].as_posix()}" if seed_image is not None else ""
        return (
            f"Video produced: {path.as_posix()} "
            f"({asset.duration_seconds:.2f}s, provider={asset.provider}, model={asset.model}{seeded})"
        )

    def _clip(self, content: str) -> str:
        """Cut one or more real clips from an existing, already-referenced
        source video - the mechanical half of the video-clipping
        checkpoint. ``content`` must already name a real source video
        path and one or more explicit ``start-end`` ranges
        (``parse_clip_specs``'s own canonical format) - resolving a
        natural-language request ("the funny bits") into those explicit
        ranges is the capability layer's own job
        (``src.manager.capabilities.clip_video``), never this tool's:
        a Tool only ever does the mechanical work it is precisely told to
        do (see this module's own docstring).
        """
        video_paths = _VIDEO_PATH_PATTERN.findall(content)
        if not video_paths:
            raise ValueError(
                "clipping needs a real source video path in the request; none was found"
            )
        source_path = Path(video_paths[0])
        if not source_path.exists():
            raise ValueError(f"the referenced source video does not exist: {source_path}")

        specs = parse_clip_specs(content)
        if not specs:
            raise ValueError(
                "clipping needs at least one explicit 'start-end' time range "
                "(e.g. '1:20-1:45'); none was found in the request"
            )

        source_bytes = source_path.read_bytes()
        clipped = self._clipper.clip_many(source_bytes, specs)

        lines = [f"{len(clipped)} clip(s) produced from {source_path.as_posix()}:"]
        for item in clipped:
            output_path = self._write(".mp4", item.video.data)
            lines.append(
                f"  {output_path.as_posix()} "
                f"({item.spec.start_seconds:.2f}s-{item.spec.end_seconds:.2f}s, "
                f"{item.video.duration_seconds:.2f}s)"
            )
        return "\n".join(lines)

    def _compose(self, content: str) -> str:
        asset_paths = [Path(m) for m in dict.fromkeys(_ASSET_PATH_PATTERN.findall(content))]
        images = [p for p in asset_paths if p.suffix.lower() == ".png"]
        audios = [p for p in asset_paths if p.suffix.lower() == ".wav"]
        if not images or not audios:
            raise ValueError(
                "composing a video needs at least one produced image (.png) "
                "and one produced narration (.wav) asset path in the request; "
                f"found images={len(images)} audios={len(audios)}"
            )
        if len(images) != len(audios):
            raise ValueError(
                f"composing a video needs one image per narration clip; "
                f"found {len(images)} image(s) and {len(audios)} narration clip(s)"
            )

        scenes = []
        for image_path, audio_path in zip(images, audios):
            if not image_path.exists() or not audio_path.exists():
                raise ValueError(f"a referenced asset does not exist: {image_path}, {audio_path}")
            audio_bytes = audio_path.read_bytes()
            duration = _wav_duration_seconds(audio_bytes)
            scenes.append(
                VideoScene(image=image_path.read_bytes(), audio=audio_bytes, duration_seconds=duration)
            )

        video = self._composer.compose(scenes)
        output_path = self._write(".mp4", video.data)
        return (
            f"Video produced: {output_path.as_posix()} "
            f"({video.duration_seconds:.2f}s, {len(scenes)} scene(s))"
        )


def _wav_duration_seconds(data: bytes) -> float:
    import io
    import wave

    with wave.open(io.BytesIO(data), "rb") as handle:
        frames = handle.getnframes()
        rate = handle.getframerate()
    return frames / float(rate)
