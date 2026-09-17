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
description", "compose these assets into a video", "generate an AI
video of this", "cut a real clip from this video", or "transcribe this
video's own real audio" - and does exactly that, through whichever real
``NarrationProvider``/``ImageProvider``/``VideoComposer``/
``VideoGenerationProvider``/``VideoClipper``/``TranscriptionProvider``
it was constructed with.

Registered under the tool name ``media_production`` - one name for all
six operations, the same reason ``live_information`` is one tool
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
from src.providers.media.transcription import NullTranscriptionProvider, TranscriptionProvider
from src.providers.media.video import FFmpegVideoComposer, VideoComposer, VideoScene, extract_audio_wav
from src.providers.media.video_clipping import ClipSpec, NullVideoClipper, VideoClipper
from src.providers.media.video_generation import NullVideoGenerationProvider, VideoGenerationProvider
from src.services.artifacts.models import AudioContent, ImageContent, VideoContent
from src.services.artifacts.registration import register_new_artifact
from src.services.artifacts.store import ArtifactStore

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
# Pre-Stage-9 Campaign 3: any recorded or uploaded audio or video a
# transcription can be taken from - audio containers as well as video.
_MEDIA_PATH_PATTERN = re.compile(
    r"\S+\.(?:mp4|mov|mkv|webm|avi|wav|mp3|m4a|ogg|oga|opus|flac|aac)", re.IGNORECASE
)
# One or more "<start>-<end>" ranges, each side either a bare number of
# seconds ("130.5") or an H:MM:SS/M:SS timecode ("1:02:10", "2:40") - the
# canonical, single format this tool's own content string is expected to
# already be in. Turning "from 2:10 to 2:40" or "the funny bit" into this
# shape is the capability layer's own job (natural-language extraction,
# and where needed, Brain reasoning over given text) - never this tool's,
# which only ever does the mechanical cut.
_TIME_RANGE_PATTERN = re.compile(
    # "1:20-1:45" or, as people say it, "from 0:00 to 0:02".
    r"((?:\d+:)?(?:\d+:)?\d+(?:\.\d+)?)\s*(?:-|to|until|through)\s*((?:\d+:)?(?:\d+:)?\d+(?:\.\d+)?)"
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


def find_media_path(content: str) -> str | None:
    """The first referenced audio or video path in ``content``, or None."""
    match = _MEDIA_PATH_PATTERN.search(content)
    return match.group(0) if match else None


def _source_and_rest(content: str, pattern: "re.Pattern[str]") -> "tuple[Path | None, str]":
    """The source file a request names, and the rest of the request with that
    path removed: an existing file on the request's own first line (how a
    capability passes a stored artifact, whose path may contain spaces),
    otherwise the first path the pattern finds.

    The rest is what time ranges are read from - never the path itself, whose
    own digits ("run-2079-4d6c") would otherwise read as a range. Found at
    runtime, Pre-Stage-9 Campaign 3."""
    text = str(content or "")
    lines = text.splitlines()
    first_line = lines[0].strip() if lines else ""
    if first_line and Path(first_line).is_file():
        return Path(first_line), "\n".join(lines[1:])
    found = pattern.findall(text)
    if not found:
        return None, text
    return Path(found[0]), text.replace(found[0], " ", 1)


def _source_path(content: str, pattern: "re.Pattern[str]") -> "Path | None":
    return _source_and_rest(content, pattern)[0]


def parse_clip_specs(content: str) -> "list[ClipSpec]":
    """Every ``ClipSpec`` named in ``content``'s own ``start-end`` ranges,
    in the order they appear - shared by ``MediaProductionTool._clip``
    and, directly, by ``src.manager.capabilities.clip_video``, which
    needs the same parse to decide *whether* a request already names
    explicit boundaries before ever reaching this tool (see that
    capability's own docstring for why that distinction matters)."""
    return [
        ClipSpec(start_seconds=_parse_timecode(start), end_seconds=_parse_timecode(end))
        for start, end in _TIME_RANGE_PATTERN.findall(normalise_spoken_times(content))
    ]


# How people say a time range out loud, rewritten to the timecodes the range
# pattern reads: "second 1 to second 4" -> "1 to 4", "2 seconds to 5 seconds"
# -> "2 to 5", "1 minute 20" -> "1:20", "between 2 and 6 seconds" -> "2 to 6".
# Units and grammar only - no video, subject or phrase list.
_MINUTES_AND_SECONDS = re.compile(
    r"\b(\d+)\s*(?:minutes?|mins?)\s*(?:and\s+)?(\d{1,2})\s*(?:seconds?|secs?|s)?\b", re.IGNORECASE
)
_WHOLE_MINUTES = re.compile(r"\b(\d+)\s*(?:minutes?|mins?)\b", re.IGNORECASE)
_SECOND_BEFORE_NUMBER = re.compile(r"\b(?:second|sec)s?\s+(\d+(?:\.\d+)?)\b", re.IGNORECASE)
_NUMBER_BEFORE_SECONDS = re.compile(r"\b(\d+(?:\.\d+)?)\s*(?:seconds?|secs?|s)\b", re.IGNORECASE)
_BETWEEN_AND = re.compile(r"\bbetween\s+((?:\d+:)?(?:\d+:)?\d+(?:\.\d+)?)\s+and\s+((?:\d+:)?(?:\d+:)?\d+(?:\.\d+)?)", re.IGNORECASE)


def normalise_spoken_times(content: str) -> str:
    text = _MINUTES_AND_SECONDS.sub(lambda m: f"{int(m.group(1))}:{int(m.group(2)):02d}", str(content or ""))
    text = _WHOLE_MINUTES.sub(lambda m: f"{int(m.group(1))}:00", text)
    text = _SECOND_BEFORE_NUMBER.sub(r"\1", text)
    text = _NUMBER_BEFORE_SECONDS.sub(r"\1", text)
    return _BETWEEN_AND.sub(r"\1 to \2", text)


class MediaProductionTool(Tool):
    """Wraps a narration provider, an image provider, a video composer,
    an AI video-generation provider, a video clipper and a
    speech-transcription provider behind the ordinary ``Tool`` contract -
    one tool, six operations, each a genuine, real production or
    understanding step.

    **Multimodal workspace/artifact checkpoint.** When constructed with
    both ``artifacts`` and ``owner_agent``, every real narration, image,
    composed video, AI-generated video and video clip this tool actually
    produces is also registered as a real, persistent, versioned
    ``Artifact`` (``src/services/artifacts/store.py``) - the identical
    store ``ArtifactProductionTool``'s own document/image artifacts
    already live in, never a second one. Omitted (the default, for
    every existing construction that predates this), this tool behaves
    exactly as it always has: real files, written to ``asset_dir``,
    with no artifact record - honest, additive, and never required.
    Registration failure is never allowed to undo or hide a real
    production that already succeeded; the tool's own real output
    (evidence a later objective-loop step needs) is always returned
    regardless of whether a record was also kept.
    """

    def __init__(
        self,
        narrator: NarrationProvider,
        illustrator: ImageProvider,
        composer: VideoComposer,
        video_generator: VideoGenerationProvider | None = None,
        clipper: VideoClipper | None = None,
        transcriber: TranscriptionProvider | None = None,
        *,
        voice_id: str = "am_eric",
        asset_dir: Path | None = None,
        artifacts: "ArtifactStore | None" = None,
        owner_agent: str = "",
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
        # Video-understanding checkpoint: the identical optional/additive
        # default, for the identical reason.
        self._transcriber = transcriber or NullTranscriptionProvider()
        self._voice_id = voice_id
        self._asset_dir = asset_dir or DEFAULT_ASSET_DIR
        self._artifacts = artifacts
        self._owner_agent = owner_agent

    @property
    def spec(self) -> ToolSpec:
        return ToolSpec(
            name="media_production",
            description=(
                "Produces one real media asset per call - narrate text into "
                "audio, illustrate a description into an image, compose "
                "already-produced assets into a video, generate a real AI "
                "video directly, cut real clips from an existing video, or "
                "transcribe an existing video's own real audio into a "
                "timestamped transcript - never a fabricated or placeholder "
                "file, and never invented transcript text."
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
        if operation == "transcribe":
            return self._transcribe(content)
        raise ValueError(
            f"unknown media_production operation {operation!r}; expected one of: "
            "narrate, illustrate, compose, generate_video, clip, transcribe"
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

    def _register(
        self,
        *,
        type: str,
        title: str,
        content_bytes: bytes,
        rendition_format: str,
        rendition_path: str,
        related_artifact_ids: "tuple[str, ...]" = (),
    ) -> None:
        """Record a real production as a real artifact, when this tool
        was actually given somewhere to record one - see this class's
        own docstring. ``rendition_path`` is the real file ``_write``
        already produced under ``asset_dir`` - referenced here, never
        copied again, so a real video/audio file is never duplicated on
        disk merely to also have an artifact record. A real production
        that already succeeded is never undone by this failing or being
        skipped; this is always additive evidence, never the
        operation's own success condition.
        """
        if self._artifacts is None or not self._owner_agent:
            return
        register_new_artifact(
            self._artifacts,
            type=type,
            owner_agent=self._owner_agent,
            title=title[:80] or type,
            content_bytes=content_bytes,
            rendition_format=rendition_format,
            rendition_path=rendition_path,
            related_artifact_ids=related_artifact_ids,
        )

    def _narrate(self, text: str) -> str:
        text = text.strip()
        if not text:
            raise ValueError("narration text must not be empty")
        asset = self._narrator.synthesize(text, self._voice_id)
        path = self._write(".wav", asset.audio)
        self._register(
            type="audio", title=text, content_bytes=AudioContent(description=text).to_json().encode("utf-8"),
            rendition_format="wav", rendition_path=path.as_posix(),
        )
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
        self._register(
            type="image", title=description,
            content_bytes=ImageContent(prompt=description).to_json().encode("utf-8"),
            rendition_format="png", rendition_path=path.as_posix(),
        )
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
        description = f"AI-generated (provider={asset.provider}, model={asset.model}): {prompt or 'image-to-video'}"
        related = ()
        if seed_image is not None and self._artifacts is not None:
            seed_artifact = self._artifacts.find_by_rendition_path(seed_paths[0].as_posix())
            if seed_artifact is not None:
                related = (seed_artifact.artifact_id,)
        self._register(
            type="video", title=prompt or "AI-generated video",
            content_bytes=VideoContent(description=description).to_json().encode("utf-8"),
            rendition_format="mp4", rendition_path=path.as_posix(), related_artifact_ids=related,
        )
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
        source_path, rest = _source_and_rest(content, _VIDEO_PATH_PATTERN)
        if source_path is None:
            raise ValueError(
                "clipping needs a real source video path in the request; none was found"
            )
        if not source_path.exists():
            raise ValueError(f"the referenced source video does not exist: {source_path}")

        specs = parse_clip_specs(rest)
        if not specs:
            raise ValueError(
                "clipping needs at least one explicit 'start-end' time range "
                "(e.g. '1:20-1:45'); none was found in the request"
            )

        source_bytes = source_path.read_bytes()
        clipped = self._clipper.clip_many(source_bytes, specs)

        # A clip is a genuinely new, derived artifact - never a new
        # version of the source (the source is explicitly untouched) -
        # linked back to it only when the source itself happens to
        # already be a tracked artifact (see ArtifactStore.own
        # find_by_rendition_path's own docstring for why this is
        # honestly best-effort, not guaranteed).
        source_artifact_id = None
        if self._artifacts is not None:
            found = self._artifacts.find_by_rendition_path(source_path.as_posix())
            source_artifact_id = found.artifact_id if found is not None else None

        lines = [f"{len(clipped)} clip(s) produced from {source_path.as_posix()}:"]
        for item in clipped:
            output_path = self._write(".mp4", item.video.data)
            description = (
                f"clip of {source_path.as_posix()} from "
                f"{item.spec.start_seconds:.2f}s to {item.spec.end_seconds:.2f}s"
            )
            self._register(
                type="video", title=description,
                content_bytes=VideoContent(description=description).to_json().encode("utf-8"),
                rendition_format="mp4", rendition_path=output_path.as_posix(),
                related_artifact_ids=(source_artifact_id,) if source_artifact_id else (),
            )
            lines.append(
                f"  {output_path.as_posix()} "
                f"({item.spec.start_seconds:.2f}s-{item.spec.end_seconds:.2f}s, "
                f"{item.video.duration_seconds:.2f}s)"
            )
        return "\n".join(lines)

    def _transcribe(self, content: str) -> str:
        """Derive a real, timestamped transcript from an existing,
        already-referenced source video's own actual audio - the
        genuine-understanding half of the video-understanding
        checkpoint's own pipeline (``video -> audio extraction ->
        speech/audio analysis -> temporal understanding``).

        ``content`` must already name a real source video path -
        resolving a natural-language request into that reference is the
        capability layer's own job (``src.manager.capabilities.clip_video``),
        never this tool's. Local, deterministic audio extraction always
        runs (no LLM, no vendor); the transcription step itself is only
        as real as whichever ``TranscriptionProvider`` this tool was
        constructed with - ``NullTranscriptionProvider`` by default,
        which refuses honestly rather than inventing text.
        """
        source_path = _source_path(content, _MEDIA_PATH_PATTERN)
        if source_path is None:
            raise ValueError(
                "transcription needs a real source audio or video path in the request; none was found"
            )
        if not source_path.exists():
            raise ValueError(f"the referenced source audio or video does not exist: {source_path}")

        source_bytes = source_path.read_bytes()
        audio_bytes = extract_audio_wav(source_bytes)
        transcript = self._transcriber.transcribe(audio_bytes)
        return transcript.as_timestamped_text()

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

        related: list[str] = []
        if self._artifacts is not None:
            for asset_path in images + audios:
                found = self._artifacts.find_by_rendition_path(asset_path.as_posix())
                if found is not None and found.artifact_id not in related:
                    related.append(found.artifact_id)
        description = f"composed from {len(scenes)} scene(s): {', '.join(p.as_posix() for p in asset_paths)}"
        self._register(
            type="video", title=f"composed video ({len(scenes)} scene(s))",
            content_bytes=VideoContent(description=description).to_json().encode("utf-8"),
            rendition_format="mp4", rendition_path=output_path.as_posix(), related_artifact_ids=tuple(related),
        )
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
