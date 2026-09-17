"""The governed boundary between a request to translate media and the
translation outputs it is routed to (``src/services/translation/outputs.py``).

Environment access only: it reads the shared file, extracts its audio, asks the
configured transcriber what was said (and in which language), asks the
configured offline translator for each timed line, and hands the result to the
requested outputs. No reasoning machinery lives here. The source artifact is
never modified; every output is a new artifact or a display update.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from src.agents.tools.tool import Tool, ToolSpec
from src.services.translation.outputs import (
    OutputContext,
    TimedLine,
    route_translation,
    translate_segments,
    translation_outputs,
)

@dataclass(frozen=True)
class _Unchanged:
    """Words kept in the language they were spoken - captions, not translation."""

    text: str
    source: str
    target: str

    @property
    def route(self) -> "tuple[str, ...]":
        return (self.source,)


_MEDIA_FORMATS = ("mp4", "mov", "mkv", "webm", "avi", "wav", "mp3", "m4a", "ogg", "oga", "opus", "flac", "aac")
_VIDEO_FORMATS = ("mp4", "mov", "mkv", "webm", "avi")


class TranslationOutputTool(Tool):
    def __init__(self, *, interpreter: Any, transcriber: Any, artifacts: Any, owner_agent: str, display: Any = None) -> None:
        self._interpreter = interpreter
        self._transcriber = transcriber
        self._artifacts = artifacts
        self._owner_agent = owner_agent
        self._display = display

    @property
    def spec(self) -> ToolSpec:
        return ToolSpec(
            name="translation_output",
            description=(
                "Translates the speech in a shared video or recording into a target language and routes the "
                "result to outputs - subtitle files, a video with a subtitle track, translated speech, a live display."
            ),
            schema={"operation": str, "content": str},
        )

    def run(self, **arguments: str) -> str:
        operation = arguments["operation"].strip().lower()
        content = arguments["content"]
        if operation == "outputs":
            return json.dumps(list(translation_outputs()))
        if operation == "translate_media":
            return self._translate_media(json.loads(content))
        if operation == "display":
            request = json.loads(content)
            lines = [TimedLine(0.0, 0.0, str(x.get("heard", "")), str(x.get("text", ""))) for x in request.get("lines", [])]
            if self._display is None:
                raise ValueError("no display surface is attached")
            self._display.publish(lines, source=str(request.get("source", "")), target=str(request.get("target", "")),
                                  origin=str(request.get("origin", "")))
            return json.dumps({"lines": len(lines)})
        raise ValueError(f"unknown translation_output operation {operation!r}; expected outputs, translate_media or display")

    def _translate_media(self, request: dict) -> str:
        from src.providers.media.video import extract_audio_wav

        interpreter = self._interpreter
        if request.get("target") and (interpreter is None or not getattr(interpreter, "translates", False)):
            raise ValueError("offline translation is not enabled (in the Control Center: provider translation_provider use argos)")
        if self._transcriber is None or getattr(self._transcriber, "name", "null") == "null":
            raise ValueError(
                "no speech recognition is configured to hear the media "
                "(in the Control Center: provider video_understanding_provider use local_whisper)"
            )
        artifact = self._artifacts.get(str(request.get("artifact_id") or "").strip().lower())
        if artifact is None or artifact.owner_agent != self._owner_agent:
            raise ValueError("no shared media with that id")
        renditions = artifact.latest.renditions
        fmt = next((f for f in _MEDIA_FORMATS if f in renditions), None)
        if fmt is None or not Path(renditions[fmt]).is_file():
            raise ValueError(f"'{artifact.latest.title}' has no audio or video to translate")
        data = Path(renditions[fmt]).read_bytes()
        is_video = fmt in _VIDEO_FORMATS
        audio = extract_audio_wav(data) if is_video or fmt != "wav" else data
        transcript = self._transcriber.transcribe(audio)
        source_code = str(transcript.language or "").strip() or None
        if request.get("target"):
            target_code = interpreter._resolve(request.get("target")).code
            translate = interpreter.translate_text
        else:
            # Captions in the language spoken: the recognised words themselves.
            target_code = source_code or ""

            def translate(words: str, _target: str, _source: "str | None") -> _Unchanged:
                return _Unchanged(words, source_code or "", source_code or "")

        media = translate_segments(transcript.segments, source=source_code or "", target=target_code, translate=translate)
        ctx = OutputContext(
            artifacts=self._artifacts, owner_agent=self._owner_agent, source_title=artifact.latest.title,
            source_bytes=data, source_is_video=is_video, speak=getattr(interpreter, "speak", None), display=self._display,
        )
        outputs = [str(o) for o in (request.get("outputs") or ["subtitles", "display"])]
        results = route_translation(media, outputs, ctx)
        return json.dumps({
            "source_title": artifact.latest.title, "source_language": media.source_language,
            "target_language": media.target_language, "route": list(media.route),
            "lines": [{"start": l.start_seconds, "end": l.end_seconds, "heard": l.heard, "text": l.text} for l in media.lines],
            "outputs": results,
        })
