"""Local speech recognition in many languages - faster-whisper (CTranslate2) on
this machine, no vendor, no key, no network.

A second local backend on the transcription axis beside Windows' own
recogniser: it hears any of Whisper's languages and names the language it
heard, which is what live translation needs. The model files live under
``data/models/stt/faster-whisper-base`` (not tracked). Measured on this laptop
(2 cores): about three seconds to load, a few seconds per short utterance.
"""

from __future__ import annotations

import importlib.util
import io
import os
import threading
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from src.providers.media.transcription import (
    Transcript,
    TranscriptionError,
    TranscriptionProvider,
    TranscriptSegment,
)

DEFAULT_MODEL_PATH = "data/models/stt/faster-whisper-base"
_MODELS: "dict[str, Any]" = {}
_LOAD_LOCK = threading.Lock()


@dataclass(frozen=True)
class Heard:
    text: str
    language: str
    probability: float
    seconds: float


class LocalWhisperTranscriptionProvider(TranscriptionProvider):
    def __init__(self, model_path: str = DEFAULT_MODEL_PATH, *, threads: "int | None" = None, model: Any = None) -> None:
        self._model_path = str(model_path)
        self._threads = threads or max(1, os.cpu_count() or 1)
        self._model = model

    @property
    def name(self) -> str:
        return "local_whisper"

    @staticmethod
    def installed(model_path: str = DEFAULT_MODEL_PATH) -> bool:
        return (Path(model_path) / "model.bin").is_file() and importlib.util.find_spec("faster_whisper") is not None

    def _loaded(self) -> Any:
        if self._model is not None:
            return self._model
        with _LOAD_LOCK:
            model = _MODELS.get(self._model_path)
            if model is None:
                if not self.installed(self._model_path):
                    raise TranscriptionError(f"the local speech model is not installed at {self._model_path}")
                from faster_whisper import WhisperModel

                model = WhisperModel(self._model_path, device="cpu", compute_type="int8", cpu_threads=self._threads)
                _MODELS[self._model_path] = model
        self._model = model
        return model

    def _run(self, audio: bytes, language: "str | None") -> "tuple[list[Any], Any]":
        if not audio:
            raise TranscriptionError("audio must not be empty")
        model = self._loaded()
        try:
            segments, info = model.transcribe(
                io.BytesIO(audio), beam_size=1, language=language, condition_on_previous_text=False,
            )
            return list(segments), info
        except TranscriptionError:
            raise
        except Exception as exc:  # noqa: BLE001 - decoding or recognition failing is reported, never hidden
            raise TranscriptionError(f"local speech recognition failed: {type(exc).__name__}: {exc}") from exc

    def recognise(self, audio: bytes, *, language: "str | None" = None) -> Heard:
        segments, info = self._run(audio, language)
        text = " ".join(segment.text.strip() for segment in segments if segment.text.strip())
        return Heard(
            text=text,
            language=str(getattr(info, "language", "") or ""),
            probability=float(getattr(info, "language_probability", 0.0) or 0.0),
            seconds=float(getattr(info, "duration", 0.0) or 0.0),
        )

    def transcribe(self, audio: bytes) -> Transcript:
        segments, info = self._run(audio, None)
        spans = tuple(
            TranscriptSegment(start_seconds=float(s.start), end_seconds=float(s.end), text=s.text.strip())
            for s in segments if s.text.strip()
        )
        if not spans:
            raise TranscriptionError("no speech was recognised in this audio")
        return Transcript(
            segments=spans, language=str(getattr(info, "language", "") or ""),
            provider=self.name, model=Path(self._model_path).name,
        )
