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


# Kokoro's own published voice-naming convention (hexgrad/Kokoro-82M,
# VOICES.md): the first letter of a voice id names the language and variety
# the voice was trained on, the second its gender. Provider-native metadata,
# carried in the one adapter it describes - never an Alfred-level list, and
# never a claim about any other provider. ``lang`` is the phonemizer code the
# engine is asked to speak a language with.
_KOKORO_VOICE_FAMILIES: "dict[str, tuple[str, str, str, str]]" = {
    # prefix: (language code, language name, accent/variety, engine lang)
    "a": ("en", "english", "american", "en-us"),
    "b": ("en", "english", "british", "en-gb"),
    "e": ("es", "spanish", "spanish", "es"),
    "f": ("fr", "french", "french", "fr-fr"),
    "h": ("hi", "hindi", "indian", "hi"),
    "i": ("it", "italian", "italian", "it"),
    "j": ("ja", "japanese", "japanese", "ja"),
    "p": ("pt", "portuguese", "brazilian", "pt-br"),
    "z": ("zh", "chinese", "mandarin", "cmn"),
}
_KOKORO_GENDERS = {"f": "female", "m": "male"}
# A pace a person or an identity document states, as the engine's speed.
_PACE_SPEEDS = {
    "slow": 0.8, "slower": 0.8, "measured": 0.93, "relaxed": 0.93, "calm": 0.93,
    "normal": 1.0, "steady": 1.0, "natural": 1.0,
    "brisk": 1.08, "quick": 1.15, "fast": 1.2, "faster": 1.2,
}


def _family(voice_id: str) -> "tuple[str, str, str, str] | None":
    return _KOKORO_VOICE_FAMILIES.get(str(voice_id or "")[:1].lower())


def _language_code(value: object) -> "str | None":
    """A spoken-language setting ("italian", "it", "Italian") as a code this
    adapter's convention knows, or None."""
    token = " ".join(str(value or "").split()).lower()
    if not token:
        return None
    for code, name, _accent, _lang in _KOKORO_VOICE_FAMILIES.values():
        if token in (code, name):
            return code
    return None


class KokoroSpeechAdapter(SpeechAdapter):
    """Text-to-speech via the real, local Kokoro ONNX engine. Speaking only.

    No ``listen()`` - Kokoro is a synthesis model, not a recognizer, the
    same honest stance ``ElevenLabsSpeechAdapter`` already takes for the
    half of the contract it does not implement.

    **Voice, spoken language and pace are separate.** Any installed voice can
    be asked to speak any language this engine phonemizes; a voice speaking a
    language other than the one it was trained on carries that training's
    accent (an Italian-trained voice speaking English is English with an
    Italian accent, never Italian). Every archive beside the default one in
    the same folder is read, so installing a voice is adding a file.
    """

    speaks_across_languages = True

    def __init__(
        self,
        model_path: str = DEFAULT_MODEL_PATH,
        voices_path: str = DEFAULT_VOICES_PATH,
    ) -> None:
        self._model_path = model_path
        self._voices_path = voices_path
        self._engine: Any = None
        self._styles: "dict[str, Any] | None" = None
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

    def _voice_archives(self) -> "list[Path]":
        default = Path(self._voices_path)
        found = [default] if default.is_file() else []
        folder = default.parent
        if folder.is_dir():
            found.extend(sorted(p for p in folder.glob("*.npz") if p != default))
        return found

    def _voice_styles(self) -> "dict[str, Any]":
        """Every voice array in every archive in the voices folder, by id -
        read once, first archive wins for a repeated id."""
        if self._styles is not None:
            return self._styles
        import numpy as np

        styles: "dict[str, Any]" = {}
        for archive_path in self._voice_archives():
            try:
                archive = np.load(archive_path)
                for voice_id in archive.files:
                    styles.setdefault(voice_id, archive[voice_id])
            except Exception as exc:  # noqa: BLE001 - one unreadable archive never hides the others
                logger.warning("kokoro voice archive %s could not be read: %s", archive_path, exc)
        self._styles = styles
        return styles

    def available_voices(self) -> "tuple[VoiceOption, ...]":
        """Every installed voice with the language, accent and gender Kokoro's
        own naming convention publishes for it. A voice id outside that
        convention is still offered, with those facts honestly unknown."""
        options = []
        for voice_id in sorted(self._voice_styles()):
            family = _family(voice_id)
            gender = _KOKORO_GENDERS.get(voice_id[1:2].lower()) if family else None
            options.append(VoiceOption(
                voice_id=voice_id, name=voice_id, provider=self.name,
                language=family[1] if family else None, accent=family[2] if family else None,
                locale=family[3] if family else None, gender=gender,
            ))
        return tuple(options)

    def spoken_languages(self) -> "tuple[str, ...]":
        """The languages this installation can speak: every language of the
        convention for which a voice of that language is installed (the engine
        phonemizes a language it has voices for)."""
        installed = {(_family(v) or ("", "", "", ""))[1] for v in self._voice_styles()}
        return tuple(sorted(name for name in installed if name))

    def pace_words(self) -> "tuple[str, ...]":
        return tuple(_PACE_SPEEDS)

    def _engine_or_raise(self) -> Any:
        if self._engine is not None:
            return self._engine
        from kokoro_onnx import Kokoro

        self._engine = Kokoro(self._model_path, self._voices_path)
        return self._engine

    def synthesize(self, text: str, voice: VoiceProfile | None = None) -> bytes:
        """The spoken audio for ``text`` as a complete WAV - never played.
        Raises ``SpeechUnavailable`` with a plain reason when it cannot."""
        voice_id = _voice_id_for(voice)
        if voice_id is None:
            raise SpeechUnavailable(
                f"no kokoro voice id configured for '{voice.key}'" if voice is not None
                else "no voice profile supplied and kokoro has no default voice"
            )
        styles = self._voice_styles()
        if voice_id not in styles:
            raise SpeechUnavailable(f"the kokoro voice '{voice_id}' is not installed")
        family = _family(voice_id)
        wanted = _language_code(getattr(voice, "language", "")) or "en"
        if wanted == "en":
            lang = family[3] if family and family[0] == "en" else "en-us"
        else:
            match = next((f for f in _KOKORO_VOICE_FAMILIES.values() if f[0] == wanted), None)
            if match is None or match[1] not in self.spoken_languages():
                raise SpeechUnavailable(f"kokoro cannot speak '{voice.language}' with the voices installed here")
            lang = match[3]
        speed = _PACE_SPEEDS.get(str(getattr(voice, "pace", "") or "").lower(), 1.0)
        engine = self._engine_or_raise()
        # The engine was loaded with the default archive, so a voice from it is
        # sent by id; a voice from any other archive is sent as its own style.
        chosen: Any = voice_id if voice_id in self._default_archive_ids() else styles[voice_id]
        options = {"speed": speed} if speed != 1.0 else {}
        samples, sample_rate = engine.create(text, voice=chosen, lang=lang, **options)
        return _pcm_wav_bytes(samples, sample_rate)

    def _default_archive_ids(self) -> "frozenset[str]":
        import numpy as np

        try:
            return frozenset(np.load(self._voices_path).files)
        except Exception:  # noqa: BLE001 - an unreadable default archive holds nothing
            return frozenset()

    def speak(self, text: str, voice: VoiceProfile | None = None) -> bool:
        try:
            audio = self.synthesize(text, voice)
        except SpeechUnavailable as exc:
            self._availability = Availability(state=AvailabilityState.UNAVAILABLE, reason=str(exc))
            return False
        except Exception as exc:  # noqa: BLE001 - a local ONNX engine fails in more ways than OSError
            self._availability = Availability(state=AvailabilityState.UNAVAILABLE, reason=str(exc))
            return False

        if not _play_wav_bytes(audio):
            self._availability = Availability(
                state=AvailabilityState.UNAVAILABLE,
                reason="synthesis succeeded but local playback failed",
            )
            return False

        self._availability = Availability(state=AvailabilityState.AVAILABLE)
        return True
