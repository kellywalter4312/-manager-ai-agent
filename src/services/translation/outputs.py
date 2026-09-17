"""Where a translation goes - one translated result, routed to any registered
output. Never a translator per device.

    input      speech on a microphone, a shared video or recording, typed text
    processing recognition -> language identification -> translation
    output     subtitles (WebVTT/SRT), a video carrying a subtitle track,
               translated speech, a live display surface (the page, the
               Control Center, a connected screen that reads it) - and any
               output registered later

A translated result is **timed lines** (``TimedLine``): the words as heard, the
words in the target language, and when they were said. Every output renders
those same lines its own way, so headphones, a speaker, a television or a page
are endpoints of one result rather than separate features. No language is
special: the source is what recognition detected, the target is whatever was
asked for, and the installed translation engine decides what is possible.

Outputs register by name (``register_translation_output``); the built-in ones
are plain, local operations - text files, an FFmpeg remux, the interpreter's own
voice, an in-process display registry - each reporting exactly what it produced.
A device the software cannot reach (a physical TV, earbuds) is a hardware leg of
the same path, never a reason for a different architecture.
"""

from __future__ import annotations

import subprocess
import tempfile
import threading
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Mapping, Sequence


@dataclass(frozen=True)
class TimedLine:
    start_seconds: float
    end_seconds: float
    heard: str
    text: str


@dataclass(frozen=True)
class TranslatedMedia:
    source_language: str
    target_language: str
    lines: "tuple[TimedLine, ...]"
    route: "tuple[str, ...]" = ()

    @property
    def text(self) -> str:
        return " ".join(line.text for line in self.lines if line.text)


def translate_segments(segments: Sequence[Any], *, source: str, target: str, translate: "Callable[..., Any]") -> TranslatedMedia:
    """Every timed segment translated from ``source`` into ``target`` - the
    segment's own timing kept, so subtitles line up with the speech."""
    lines = []
    route: "tuple[str, ...]" = ()
    for segment in segments:
        heard = str(getattr(segment, "text", "") or "").strip()
        if not heard:
            continue
        result = translate(heard, target, source or None)
        route = tuple(getattr(result, "route", ()) or route)
        lines.append(TimedLine(float(segment.start_seconds), float(segment.end_seconds), heard, str(result.text).strip()))
    if not lines:
        raise ValueError("there was no recognised speech to translate")
    resolved_target = str(getattr(result, "target", target))
    resolved_source = str(getattr(result, "source", source))
    return TranslatedMedia(resolved_source, resolved_target, tuple(lines), route)


def _timestamp(seconds: float, separator: str) -> str:
    millis = max(0, int(round(seconds * 1000)))
    hours, rest = divmod(millis, 3_600_000)
    minutes, rest = divmod(rest, 60_000)
    secs, millis = divmod(rest, 1000)
    return f"{hours:02d}:{minutes:02d}:{secs:02d}{separator}{millis:03d}"


def to_webvtt(media: TranslatedMedia) -> str:
    cues = ["WEBVTT", f"NOTE translated from {media.source_language} to {media.target_language}", ""]
    for index, line in enumerate(media.lines, 1):
        end = max(line.end_seconds, line.start_seconds + 0.5)
        cues += [str(index), f"{_timestamp(line.start_seconds, '.')} --> {_timestamp(end, '.')}", line.text, ""]
    return "\n".join(cues)


def to_srt(media: TranslatedMedia) -> str:
    cues = []
    for index, line in enumerate(media.lines, 1):
        end = max(line.end_seconds, line.start_seconds + 0.5)
        cues += [str(index), f"{_timestamp(line.start_seconds, ',')} --> {_timestamp(end, ',')}", line.text, ""]
    return "\n".join(cues)


class TranslationDisplay:
    """The live display surface a translation can be routed to - the latest
    lines, read by the page, the Control Center, or any connected screen that
    polls the snapshot. In-process and thread-safe; it holds what was last
    shown, never a history store."""

    def __init__(self, keep: int = 12) -> None:
        self._keep = keep
        self._lock = threading.Lock()
        self._lines: "list[dict]" = []
        self._source = ""
        self._target = ""
        self._updated = ""

    def publish(self, lines: "Sequence[TimedLine]", *, source: str, target: str, origin: str = "") -> None:
        with self._lock:
            for line in lines:
                self._lines.append({"heard": line.heard, "text": line.text, "start": line.start_seconds,
                                    "end": line.end_seconds, "origin": origin})
            self._lines = self._lines[-self._keep:]
            self._source, self._target = source, target
            self._updated = datetime.now(timezone.utc).isoformat()

    def clear(self) -> None:
        with self._lock:
            self._lines = []
            self._updated = datetime.now(timezone.utc).isoformat()

    def state(self) -> dict:
        with self._lock:
            return {"source": self._source, "target": self._target, "updated": self._updated, "lines": list(self._lines)}


@dataclass
class OutputContext:
    """What an output may use to produce its result - never more."""

    artifacts: Any
    owner_agent: str
    source_title: str
    source_bytes: "bytes | None" = None
    source_is_video: bool = False
    speak: "Callable[[str, str], tuple[bytes | None, str, str]] | None" = None
    display: "TranslationDisplay | None" = None
    produced: "dict[str, Any]" = field(default_factory=dict)


def _register(ctx: OutputContext, *, type_: str, title: str, content: bytes, fmt: str, data: bytes, kind: str, media_type: str) -> str:
    from src.services.artifacts.registration import register_new_artifact

    artifact = register_new_artifact(
        ctx.artifacts, type=type_, owner_agent=ctx.owner_agent, title=title[:120], content_bytes=content,
        rendition_format=fmt, rendition_bytes=data, metadata={"content_kind": kind, "media_type": media_type},
    )
    return artifact.artifact_id


def _subtitles(ctx: OutputContext, media: TranslatedMedia) -> dict:
    vtt, srt = to_webvtt(media), to_srt(media)
    title = f"{ctx.source_title} - {media.target_language} subtitles"
    vtt_id = _register(ctx, type_="subtitles", title=title, content=srt.encode("utf-8"), fmt="vtt",
                       data=vtt.encode("utf-8"), kind="subtitles", media_type="text/vtt")
    ctx.produced["subtitles_vtt"] = vtt
    return {"artifact_id": vtt_id, "format": "vtt", "cues": len(media.lines)}


def _captioned_video(ctx: OutputContext, media: TranslatedMedia) -> dict:
    """The source video with the translation as an embedded subtitle track
    (mov_text) - what a player, a television app or a phone shows as captions."""
    if not ctx.source_is_video or not ctx.source_bytes:
        return {"skipped": "the source is not a video"}
    from src.providers.media.video import resolve_ffmpeg  # noqa: PLC0415 - the one ffmpeg this project uses

    with tempfile.TemporaryDirectory() as work:
        source = Path(work) / "source.mp4"
        subtitles = Path(work) / "subtitles.srt"
        output = Path(work) / "captioned.mp4"
        source.write_bytes(ctx.source_bytes)
        subtitles.write_text(to_srt(media), encoding="utf-8")
        language = (media.target_language or "und")[:3]
        completed = subprocess.run(
            [resolve_ffmpeg(), "-y", "-loglevel", "error", "-i", str(source), "-i", str(subtitles),
             "-map", "0:v?", "-map", "0:a?", "-map", "1:0", "-c:v", "copy", "-c:a", "copy", "-c:s", "mov_text",
             "-metadata:s:s:0", f"language={language}", str(output)],
            capture_output=True, text=True, timeout=180,
        )
        if completed.returncode != 0 or not output.is_file():
            raise RuntimeError(f"ffmpeg could not add the subtitle track: {completed.stderr.strip()[:300]}")
        data = output.read_bytes()
    video_id = _register(ctx, type_="video", title=f"{ctx.source_title} ({media.target_language} subtitles)",
                         content=to_srt(media).encode("utf-8"), fmt="mp4", data=data, kind="video", media_type="video/mp4")
    return {"artifact_id": video_id, "format": "mp4", "bytes": len(data)}


def _audio(ctx: OutputContext, media: TranslatedMedia) -> dict:
    if ctx.speak is None:
        return {"skipped": "no voice can speak here"}
    wav, voice, spoken_by = ctx.speak(media.text, media.target_language)
    if not wav:
        return {"skipped": f"no local voice speaks {media.target_language}; a device can speak it ({spoken_by})"}
    audio_id = _register(ctx, type_="audio", title=f"{ctx.source_title} - spoken in {media.target_language}",
                         content=media.text.encode("utf-8"), fmt="wav", data=wav, kind="audio", media_type="audio/wav")
    return {"artifact_id": audio_id, "format": "wav", "voice": voice}


def _display(ctx: OutputContext, media: TranslatedMedia) -> dict:
    if ctx.display is None:
        return {"skipped": "no display surface is attached"}
    ctx.display.publish(media.lines, source=media.source_language, target=media.target_language, origin=ctx.source_title)
    return {"lines": len(media.lines)}


_OUTPUTS: "dict[str, Callable[[OutputContext, TranslatedMedia], dict]]" = {
    "subtitles": _subtitles,
    "captioned_video": _captioned_video,
    "audio": _audio,
    "display": _display,
}


def register_translation_output(name: str, handler: "Callable[[OutputContext, TranslatedMedia], dict]") -> None:
    """Add an output endpoint - a connected screen, a messaging channel, an
    earpiece bridge - with no change to translation itself."""
    key = "_".join(str(name or "").strip().lower().split())
    if not key:
        raise ValueError("an output needs a name")
    _OUTPUTS[key] = handler


def translation_outputs() -> "tuple[str, ...]":
    return tuple(_OUTPUTS)


def route_translation(media: TranslatedMedia, outputs: Sequence[str], ctx: OutputContext) -> "Mapping[str, dict]":
    results: "dict[str, dict]" = {}
    for name in outputs:
        handler = _OUTPUTS.get(name)
        if handler is None:
            results[name] = {"error": f"no output called '{name}'. Available: {', '.join(_OUTPUTS)}"}
            continue
        try:
            results[name] = handler(ctx, media)
        except Exception as exc:  # noqa: BLE001 - one output failing never hides the others
            results[name] = {"error": f"{type(exc).__name__}: {exc}"}
    return results
