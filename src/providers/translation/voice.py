"""A local neural voice that speaks more than English - Kokoro, with a project
archive of the multilingual voices this project uses
(``data/voices/kokoro/voices_multilingual.npz``, built from the vendor's
``voices-v1.0.bin``).

Returns WAV bytes rather than playing them: the person's own device plays the
translation - headphones or speaker - so the voice reaches whoever is listening,
not the server's sound card. A language with no local voice (German, today) is
honestly not speakable here; the page then uses the device's own voice for that
locale.
"""

from __future__ import annotations

import io
import threading
import wave
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Mapping

DEFAULT_MODEL_PATH = "data/voices/kokoro/kokoro-v1.0.int8.onnx"
DEFAULT_VOICES_PATH = "data/voices/kokoro/voices_multilingual.npz"


@dataclass(frozen=True)
class Spoken:
    wav: bytes
    voice: str
    seconds: float


def wav_bytes(samples: Any, rate: int) -> bytes:
    import numpy as np

    pcm = (np.clip(np.asarray(samples, dtype="float32"), -1.0, 1.0) * 32767).astype("<i2")
    buffer = io.BytesIO()
    with wave.open(buffer, "wb") as handle:
        handle.setnchannels(1)
        handle.setsampwidth(2)
        handle.setframerate(int(rate))
        handle.writeframes(pcm.tobytes())
    return buffer.getvalue()


class MultilingualVoice:
    name = "kokoro"

    def __init__(
        self, model_path: str = DEFAULT_MODEL_PATH, voices_path: str = DEFAULT_VOICES_PATH, *,
        preferred: "Mapping[str, str] | None" = None, voices: "tuple[str, ...] | None" = None, engine: Any = None,
    ) -> None:
        self._model_path = model_path
        self._voices_path = voices_path
        self._preferred = dict(preferred or {})
        self._engine = engine
        self._lock = threading.Lock()
        if voices is None:
            import numpy as np

            voices = tuple(np.load(voices_path).files)
        self._voices = tuple(voices)

    @classmethod
    def if_installed(cls, model_path: str = DEFAULT_MODEL_PATH, voices_path: str = DEFAULT_VOICES_PATH) -> "MultilingualVoice | None":
        if not (Path(model_path).is_file() and Path(voices_path).is_file()):
            return None
        try:
            import kokoro_onnx  # noqa: F401
        except ImportError:
            return None
        try:
            return cls(model_path, voices_path)
        except (OSError, ValueError):
            return None

    def voices(self) -> "tuple[str, ...]":
        return self._voices

    def voice_for(self, language: Any) -> "str | None":
        chosen = self._preferred.get(language.code)
        if chosen in self._voices:
            return chosen
        return next((voice for voice in language.voices if voice in self._voices), None)

    def can_speak(self, language: Any) -> bool:
        return bool(getattr(language, "voice_language", "")) and self.voice_for(language) is not None

    def synthesize(self, text: str, language: Any, *, voice: "str | None" = None) -> Spoken:
        chosen = voice if voice in self._voices else self.voice_for(language)
        if not chosen or not language.voice_language:
            raise ValueError(f"no local voice speaks {language.name}")
        with self._lock:
            if self._engine is None:
                from kokoro_onnx import Kokoro

                self._engine = Kokoro(self._model_path, self._voices_path)
            samples, rate = self._engine.create(text, voice=chosen, lang=language.voice_language)
        return Spoken(wav_bytes(samples, rate), chosen, len(samples) / float(rate))
