"""A third real speech engine behind the same seam - Kokoro, local neural TTS.

Exists for the same reason ``elevenlabs_speech_provider.py`` exists: a new
engine arrives as an additive change. Nothing in ``src/channels/speech.py``
or ``VoiceProfile`` changes to add this file; only this module and
``speech_factory.py``'s registry do.

**Why Kokoro, and why not the full voice catalogue.** Kokoro
(hexgrad/Kokoro-82M) is a free, open-weight, local neural TTS model - no
account, no key, no per-call cost, unlike ElevenLabs. Only the two
specific voices this project's identity documents actually declare were
ever wanted, out of the vendor's ~54 published voices. The vendor's own
combined voices file (``voices-v1.0.bin``) bundles every voice;
downloading it would mean installing voices nobody asked for. Kokoro's
upstream HuggingFace repo (``hexgrad/Kokoro-82M``) also publishes each
voice as its own small file under ``voices/`` - exactly the two wanted
were fetched from there, nothing else - and repackaged into this
project's own minimal ``data/voices/kokoro/voices.npz``, an archive
holding only those two voice arrays. ``kokoro_onnx.Kokoro`` loads
whatever ``voices_path`` names via a plain ``numpy.load`` and indexes it
by name; a two-voice archive built this way is indistinguishable to it
from the vendor's own file.

**Where the chosen voice lives, and why nothing here names an agent.**
Exactly ``elevenlabs_speech_provider.py``'s own answer: a ``VoiceProfile``
never learns a vendor exists, so a Kokoro voice id is a provider-specific
fact carried opaquely in ``VoiceProfile.style["kokoro_voice_id"]`` - read
here, written nowhere but an identity document's own ``## Voice`` section.
A future specialist gets its own Kokoro voice the same way today's two
agents do: by declaring one, not by this file learning a second name.

**Playback.** Kokoro returns raw float32 PCM and a sample rate, not a
device call. Written to a temporary WAV (the standard library's own
``wave`` module - no new dependency) and played with ``winsound``, the
same mechanism and the same honest Windows-only limitation
``elevenlabs_speech_provider.py`` already accepts for the same reason.

**Engine construction happens once per adapter instance**, not once per
utterance: loading the ONNX session and the voices archive is real,
measurable work, and every ``speak()`` call after the first reuses it -
the same amortised-cost shape ``WindowsSpeechAdapter.installed_voice_names``
already applies to its own live query.
"""

from __future__ import annotations

import struct
import tempfile
import wave
from pathlib import Path
from typing import Any

from src.channels.speech import SpeechAdapter, SpeechUnavailable
from src.providers.availability import Availability, AvailabilityState
from src.services.identity.voice import VoiceProfile
from src.services.identity.voice_options import VoiceOption
from src.utils.logging import get_logger

logger = get_logger(__name__)

# Relative to the current working directory - the same convention
# Settings.memory_dir ("data/memory") already uses, so this needs no new
# path-resolution rule of its own.
DEFAULT_MODEL_PATH = "data/voices/kokoro/kokoro-v1.0.int8.onnx"
DEFAULT_VOICES_PATH = "data/voices/kokoro/voices.npz"

_SAMPLE_WIDTH_BYTES = 2  # 16-bit PCM


def _voice_id_for(voice: VoiceProfile | None) -> str | None:
    """The Kokoro voice name this profile carries, if any.

    Read from ``style`` - never a typed ``VoiceProfile`` field - for the
    same reason ``elevenlabs_speech_provider._voice_id_for`` reads
    ``elevenlabs_voice_id`` from there: this is a provider-specific fact,
    not a characteristic ``VoiceProfile`` itself understands.
    """
    if voice is None:
        return None
    raw = voice.style.get("kokoro_voice_id")
    text = str(raw or "").strip()
    return text or None


def _pcm_wav_bytes(audio: Any, sample_rate: int) -> bytes:
    """Float32 samples in [-1, 1] into a complete, real WAV container."""
    import io

    clipped = [max(-1.0, min(1.0, float(sample))) for sample in audio]
    frames = struct.pack(f"<{len(clipped)}h", *(int(sample * 32767) for sample in clipped))
    buffer = io.BytesIO()
    with wave.open(buffer, "wb") as handle:
        handle.setnchannels(1)
        handle.setsampwidth(_SAMPLE_WIDTH_BYTES)
        handle.setframerate(sample_rate)
        handle.writeframes(frames)
    return buffer.getvalue()


def _play_wav_bytes(audio: bytes) -> bool:
    """Play a complete WAV file synchronously - blocking, exactly as
    ``elevenlabs_speech_provider._play_wav_bytes`` does, so ``speak()``
    never returns True before the audio has actually been produced.
    """
    try:
        import winsound
    except ImportError:
        logger.warning("kokoro playback requires winsound, which is not available here")
        return False

    path: str | None = None
    try:
        with tempfile.NamedTemporaryFile(suffix=".wav", delete=False) as handle:
            handle.write(audio)
            path = handle.name
        winsound.PlaySound(path, winsound.SND_FILENAME)
        return True
    except OSError as exc:
        logger.warning("kokoro playback failed: %s", exc)
        return False
    finally:
        if path is not None:
            Path(path).unlink(missing_ok=True)


class KokoroSpeechAdapter(SpeechAdapter):
    """Text-to-speech via the real, local Kokoro ONNX engine. Speaking only.

    No ``listen()`` - Kokoro is a synthesis model, not a recognizer, the
    same honest stance ``ElevenLabsSpeechAdapter`` already takes for the
    half of the contract it does not implement.
    """

    def __init__(
        self,
        model_path: str = DEFAULT_MODEL_PATH,
        voices_path: str = DEFAULT_VOICES_PATH,
    ) -> None:
        self._model_path = model_path
        self._voices_path = voices_path
        self._engine: Any = None
        self._availability = Availability()

    @property
    def name(self) -> str:
        return "kokoro"

    @property
    def availability(self) -> Availability:
        return self._availability

    def listen(self, timeout: float | None = None) -> str:
        raise SpeechUnavailable(
            "the kokoro provider is text-to-speech only; it does not "
            "listen. Type instead, or select a provider that hears."
        )

    def available_voices(self) -> "tuple[VoiceOption, ...]":
        """The real voice ids this project's own local archive genuinely
        contains - read directly from ``voices.npz``'s own keys (a plain
        numpy archive; listing its member names costs one small local
        read, never loading the ONNX engine or the arrays themselves),
        never the vendor's full ~54-voice catalog this project never
        downloaded (see this module's own docstring on why).

        **What is - and is not - honestly knowable about each voice.**
        This project has no in-repo evidence for the upstream project's
        own voice-naming convention, so no accent, locale or per-voice
        language is asserted from a voice id's shape - that would be a
        guess, not a reported fact, exactly what this checkpoint's own
        brief forbids. What *is* a genuine, verifiable fact about every
        voice this adapter can produce is ``speak()``'s own call below:
        it always passes ``lang="en-us"`` to the engine, regardless of
        which voice is chosen - a fact about this integration, not a
        claim about the vendor's own catalog, and reported as such
        (``language="english"``, ``locale="en-US"``) for every voice
        this method returns. Accent and gender are left honestly
        ``None`` - no verifiable source for either exists here.
        """
        try:
            import numpy as np

            archive = np.load(self._voices_path)
            ids = tuple(archive.files)
        except Exception as exc:  # noqa: BLE001 - a local file read fails in more ways than OSError
            logger.warning("kokoro voice archive could not be read: %s", exc)
            return ()
        return tuple(
            VoiceOption(voice_id=vid, name=vid, provider=self.name, language="english", locale="en-US")
            for vid in ids
        )

    def _engine_or_raise(self) -> Any:
        if self._engine is not None:
            return self._engine
        from kokoro_onnx import Kokoro

        self._engine = Kokoro(self._model_path, self._voices_path)
        return self._engine

    def speak(self, text: str, voice: VoiceProfile | None = None) -> bool:
        voice_id = _voice_id_for(voice)
        if voice_id is None:
            reason = (
                f"no kokoro voice id configured for '{voice.key}'"
                if voice is not None
                else "no voice profile supplied and kokoro has no default voice"
            )
            self._availability = Availability(state=AvailabilityState.UNAVAILABLE, reason=reason)
            return False

        try:
            engine = self._engine_or_raise()
            samples, sample_rate = engine.create(text, voice=voice_id, lang="en-us")
        except Exception as exc:  # noqa: BLE001 - a local ONNX engine fails in more ways than OSError
            self._availability = Availability(state=AvailabilityState.UNAVAILABLE, reason=str(exc))
            return False

        audio = _pcm_wav_bytes(samples, sample_rate)
        if not _play_wav_bytes(audio):
            self._availability = Availability(
                state=AvailabilityState.UNAVAILABLE,
                reason="synthesis succeeded but local playback failed",
            )
            return False

        self._availability = Availability(state=AvailabilityState.AVAILABLE)
        return True
