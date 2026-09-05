"""Turning written text into real, playable narration audio - a genuine
media asset, not a spoken reply.

Separate from ``src/channels/speech.py``'s ``SpeechAdapter`` on purpose,
even though both end up calling the same underlying engine: a
``SpeechAdapter`` speaks *for* an agent, synchronously, as part of a live
conversation turn (``speak()`` blocks, plays through the machine's own
audio device, and returns whether it was heard). A ``NarrationProvider``
produces a *file* - a durable asset another process (a video composer)
consumes later, never played here, never tied to any conversation turn
or agent identity. Reusing ``SpeechAdapter`` for this would either force
narration to play out loud on the machine generating it, or force
``speak()`` to grow a "don't actually play this" mode neither of its
two real callers need.

Provider-neutral, the identical shape ``LLMProvider`` and
``SpeechAdapter`` already established: an abstract contract, a real
local implementation, and an honest null default. Nothing here is
specific to video, a script, a scene, or any creative genre - "turn
this text into narration audio" is a single, general operation, usable
anywhere a real audio file is needed.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field

from src.providers.kokoro_speech_provider import _pcm_wav_bytes


class NarrationError(RuntimeError):
    """Raised when narration audio could not be produced."""


@dataclass(frozen=True)
class NarrationAsset:
    """Real, playable audio - WAV bytes, never a placeholder.

    ``audio`` is a complete WAV container (see
    ``kokoro_speech_provider._pcm_wav_bytes``), so any caller can write
    it to disk or pipe it into a composer without knowing anything about
    how it was produced.
    """

    audio: bytes = field(repr=False)
    sample_rate: int
    duration_seconds: float
    voice_id: str

    def __post_init__(self) -> None:
        if not self.audio:
            raise NarrationError("NarrationAsset.audio must not be empty")
        if self.sample_rate <= 0:
            raise NarrationError("NarrationAsset.sample_rate must be positive")
        if self.duration_seconds <= 0:
            raise NarrationError("NarrationAsset.duration_seconds must be positive")


class NarrationProvider(ABC):
    """A source of real narration audio from written text."""

    @property
    @abstractmethod
    def name(self) -> str:
        """Short identifier, used in logs and configuration."""

    @abstractmethod
    def synthesize(self, text: str, voice_id: str) -> NarrationAsset:
        """Return real audio for ``text``, spoken as ``voice_id``.

        Implementations must raise ``NarrationError`` (or let a more
        specific exception propagate) on an empty text or a genuine
        synthesis failure - never return silence or a placeholder tone
        standing in for speech that was never produced.
        """

    def __repr__(self) -> str:
        return f"<{type(self).__name__} name={self.name!r}>"


class NullNarrationProvider(NarrationProvider):
    """The default: no narration engine configured. Honest, not silent -
    the same role ``NullProvider``/``NoSpeech`` already play one layer
    up."""

    @property
    def name(self) -> str:
        return "null"

    def synthesize(self, text: str, voice_id: str) -> NarrationAsset:
        raise NarrationError(
            "no narration engine is configured, so I cannot produce real "
            "audio - I would rather say so than invent a placeholder"
        )


class KokoroNarrationProvider(NarrationProvider):
    """Real, local, offline narration via the same Kokoro ONNX engine
    ``KokoroSpeechAdapter`` already uses for spoken replies - genuinely
    installed in this project (see ``data/voices/kokoro/``), and reused
    here rather than reimplemented: the engine construction and the WAV
    encoding are the identical calls, only what happens to the result
    (returned as an asset vs. played aloud) differs.
    """

    def __init__(self, model_path: str, voices_path: str) -> None:
        self._model_path = model_path
        self._voices_path = voices_path
        self._engine = None

    @property
    def name(self) -> str:
        return "kokoro"

    def _engine_or_raise(self):
        if self._engine is not None:
            return self._engine
        from kokoro_onnx import Kokoro  # imported lazily, matching every other

        self._engine = Kokoro(self._model_path, self._voices_path)
        return self._engine

    def synthesize(self, text: str, voice_id: str) -> NarrationAsset:
        if not text.strip():
            raise NarrationError("narration text must not be empty")
        if not voice_id.strip():
            raise NarrationError("a voice_id must be given")

        try:
            engine = self._engine_or_raise()
            samples, sample_rate = engine.create(text, voice=voice_id, lang="en-us")
        except Exception as exc:  # noqa: BLE001 - deliberate boundary catch
            raise NarrationError(f"narration synthesis failed: {type(exc).__name__}: {exc}") from exc

        audio = _pcm_wav_bytes(samples, sample_rate)
        duration = len(samples) / float(sample_rate)
        return NarrationAsset(
            audio=audio, sample_rate=sample_rate, duration_seconds=duration, voice_id=voice_id
        )
