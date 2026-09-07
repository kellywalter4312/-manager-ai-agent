"""Extracting real clips from an existing video file - genuinely
different work from ``video.py``'s ``VideoComposer`` (assembles new
scenes into a video) and ``video_generation.py``'s
``VideoGenerationProvider`` (asks an AI model to synthesise a new video
from a prompt). Clipping starts from a video that already exists and
produces a real, shorter file cut from it - deterministic media
processing, never a model call, and never a placeholder description of
what a clip would contain.

**Local, like composition - never a vendor.** Trimming a real file at
real timestamps is exactly the kind of operation FFmpeg (already
vendored via ``imageio-ffmpeg``, see ``video.py``'s own docstring) has
always done correctly; inventing a "clipping provider" abstraction with
a null/real vendor split, the shape every genuinely *external* service
in this project takes, would misrepresent local processing as a
credentialed dependency it is not. The one real implementation here
(``FFmpegVideoClipper``) is this capability's whole story - the
``VideoClipper`` ABC and ``NullVideoClipper`` exist anyway, for the
identical reason every other real capability in this project keeps its
"honestly unconfigured" default: a deployment that has not installed
``imageio-ffmpeg`` gets a clear refusal, never a crash three layers
down.

**Half Brain safe, deliberately.** Given explicit start/end timestamps,
producing a clip needs no LLM at all - the same "deterministic
operations should not unnecessarily require an LLM" rule this project
already holds Tasks and every other non-conversational capability to.
AI-*guided* selection of which timestamps to use is a separate,
higher-level concern (see ``src/manager/capabilities.py::clip_video``)
that may consult the Brain over given text; this module never does.
"""

from __future__ import annotations

import tempfile
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from pathlib import Path

from src.providers.media.video import VideoAsset, VideoError, probe_duration_seconds, resolve_ffmpeg, run_ffmpeg


class ClippingError(RuntimeError):
    """Raised when a clip could not be produced from a source video."""


@dataclass(frozen=True)
class ClipSpec:
    """One requested clip: a real time range within a real source video.

    ``label``, when given, is provenance - a short, caller-supplied name
    ("funniest moment", "intro") carried through to the produced asset's
    own description, never interpreted or acted on here.
    """

    start_seconds: float
    end_seconds: float
    label: str = ""

    def __post_init__(self) -> None:
        if self.start_seconds < 0:
            raise ClippingError("a clip's start_seconds must not be negative")
        if self.end_seconds <= self.start_seconds:
            raise ClippingError(
                "a clip's end_seconds must be after its start_seconds "
                f"(got start={self.start_seconds}, end={self.end_seconds})"
            )

    @property
    def duration_seconds(self) -> float:
        return self.end_seconds - self.start_seconds


@dataclass(frozen=True)
class ClippedVideoAsset:
    """One real, produced clip, alongside which requested spec it came
    from - the same "evidence, not just a file" shape ``GeneratedVideoAsset``
    already carries for AI-generated video."""

    video: VideoAsset
    spec: ClipSpec


class VideoClipper(ABC):
    """Produces real clips from a real, already-existing source video."""

    @property
    @abstractmethod
    def name(self) -> str:
        """Short identifier, used in logs and configuration."""

    @abstractmethod
    def clip(self, source: bytes, spec: ClipSpec) -> VideoAsset:
        """Return one real, playable clip covering ``spec``'s time range
        within ``source``.

        Implementations must raise ``ClippingError`` when ``spec``'s
        range exceeds the source's own real, decoded duration, or on a
        genuine processing failure - never return an empty file, the
        whole source unchanged, or a placeholder standing in for a clip
        that was never actually cut.
        """

    def clip_many(self, source: bytes, specs: "list[ClipSpec]") -> "list[ClippedVideoAsset]":
        """Every requested clip from the same source, in order - the
        real extension point "five separate clips from one source"
        needs, built once here so every ``VideoClipper`` implementation
        gets it for free rather than reimplementing a loop.

        Deliberately not parallelised: each call already re-probes and
        re-decodes the source through a real subprocess, and running
        several at once would only contend for the same CPU/FFmpeg
        binary faster, not produce a clip sooner.
        """
        if not specs:
            raise ClippingError("at least one clip specification is required")
        return [ClippedVideoAsset(video=self.clip(source, spec), spec=spec) for spec in specs]

    def __repr__(self) -> str:
        return f"<{type(self).__name__} name={self.name!r}>"


class NullVideoClipper(VideoClipper):
    """The default: no clipping engine configured (``imageio-ffmpeg``
    missing) - honest, not silent, the same role every other Null*
    provider in this project already plays. In practice this is rare:
    unlike a credentialed vendor, FFmpeg has no key to withhold, so this
    path is reached only by a genuinely broken/incomplete install."""

    @property
    def name(self) -> str:
        return "null"

    def clip(self, source: bytes, spec: ClipSpec) -> VideoAsset:
        raise ClippingError(
            "no video-clipping engine is configured, so I cannot produce "
            "a real clip - I would rather say so than invent one"
        )


class FFmpegVideoClipper(VideoClipper):
    """Real clipping via a genuine FFmpeg binary.

    Re-encodes rather than stream-copies. A copy-mode cut
    (``-c copy``) is faster but can only cut on the source's own
    keyframes, silently shifting a requested boundary by up to a few
    seconds - exactly the kind of "close enough" this project's own
    honesty discipline refuses (see ``probe_duration_seconds``'s own
    docstring: "never a vendor's claimed or estimated value taken on
    faith"). Re-encoding costs time, not correctness, and is the
    identical trade-off ``FFmpegVideoComposer`` already makes for every
    scene it produces.
    """

    def __init__(self, ffmpeg_path: str | None = None) -> None:
        self._ffmpeg_path = ffmpeg_path

    @property
    def name(self) -> str:
        return "ffmpeg"

    def clip(self, source: bytes, spec: ClipSpec) -> VideoAsset:
        ffmpeg = resolve_ffmpeg(self._ffmpeg_path)

        with tempfile.TemporaryDirectory(prefix="alfred_clip_") as workdir:
            work = Path(workdir)
            source_path = work / "source.mp4"
            source_path.write_bytes(source)

            # The real, decoded duration - never the caller's own claim
            # about how long the source runs - so a requested range past
            # the actual end of the file is refused honestly rather than
            # silently truncated by FFmpeg into a shorter clip than asked
            # for.
            real_duration = probe_duration_seconds(source, ffmpeg_path=self._ffmpeg_path)
            if spec.end_seconds > real_duration + 0.5:  # tolerate sub-second probe rounding
                raise ClippingError(
                    f"the requested clip ({spec.start_seconds:.2f}s-{spec.end_seconds:.2f}s) "
                    f"extends past the source video's real length ({real_duration:.2f}s)"
                )

            output_path = work / "clip.mp4"
            run_ffmpeg(
                ffmpeg,
                [
                    "-ss", f"{spec.start_seconds:.3f}",
                    "-i", str(source_path),
                    "-t", f"{spec.duration_seconds:.3f}",
                    "-c:v", "libx264",
                    "-c:a", "aac",
                    "-pix_fmt", "yuv420p",
                    str(output_path),
                ],
            )
            data = output_path.read_bytes()

        return VideoAsset(data=data, format="mp4", duration_seconds=spec.duration_seconds)
