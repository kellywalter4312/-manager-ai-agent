"""Alfred's interpreter - natural translation, spoken or written, woven into his
voice, conversation and Control Center rather than a separate translator app.

One object joins three independently replaceable collaborators, each duck-typed
so this service imports no provider:

    translator  offline text translation (``pairs()``, ``languages()``,
                ``can_translate(src, dst)``, ``translate(text, src, dst)``)
    listener    speech recognition that also detects the spoken language
                (``recognise(audio) -> text, language, probability, seconds``) -
                the transcription axis's own provider when it can do this
    voice       a local voice that speaks a language (``can_speak``,
                ``synthesize``); when it cannot, the page speaks with the
                device's own voice for that locale, and says so

**Conversational.** ``interpret_speech`` with a language pair translates each
utterance *towards the other person*: heard in the first language, it is spoken
in the second, and the reverse - so two people take turns through headphones or
a device speaker without choosing a direction every time.

**Honest.** Every refusal names the missing piece and where the owner turns it
on. Nothing is guessed: an utterance in a language that cannot be translated is
refused, and no audio is ever described as spoken when it was not produced.
"""

from __future__ import annotations

import base64
import time
from dataclasses import dataclass, field
from typing import Any, Sequence

from src.services.translation.languages import LANGUAGES, Language, all_languages, guess_language, language_by_code, language_named

ENABLE_TRANSLATION = "in the Control Center: provider translation_provider use argos"
ENABLE_LISTENING = "in the Control Center: provider video_understanding_provider use local_whisper"


class InterpretationError(LookupError):
    """A plain, person-facing reason translation could not happen."""


@dataclass(frozen=True)
class InterpretedTurn:
    heard: str
    source: str
    source_probability: float
    translation: str
    target: str
    route: tuple[str, ...]
    audio_wav: "bytes | None" = None
    voice: str = ""
    spoken_by: str = "none"
    seconds: "dict[str, float]" = field(default_factory=dict)

    def to_dict(self) -> dict:
        source, target = language_by_code(self.source), language_by_code(self.target)
        return {
            "heard": self.heard,
            "source": self.source,
            "source_name": source.name if source else self.source,
            "source_probability": round(self.source_probability, 3),
            "translation": self.translation,
            "target": self.target,
            "target_name": target.name if target else self.target,
            "target_locale": target.locale if target else self.target,
            "route": list(self.route),
            "audio_base64": base64.b64encode(self.audio_wav).decode("ascii") if self.audio_wav else None,
            "audio_format": "wav" if self.audio_wav else None,
            "voice": self.voice,
            "spoken_by": self.spoken_by,
            "seconds": {key: round(value, 2) for key, value in self.seconds.items()},
        }


class Interpreter:
    def __init__(self, translator: Any = None, listener: Any = None, voice: Any = None) -> None:
        self._translator = translator
        self._listener = listener
        self._voice = voice

    # ---- what is equipped ------------------------------------------------

    @property
    def translates(self) -> bool:
        return self._translator is not None and bool(self._translator.pairs())

    @property
    def listens(self) -> bool:
        return self._listener is not None and callable(getattr(self._listener, "recognise", None))

    @property
    def speaks(self) -> bool:
        return self._voice is not None

    def _translatable_codes(self) -> "set[str]":
        return set(self._translator.languages()) if self.translates else set()

    def status(self) -> dict:
        codes = self._translatable_codes()
        reason = "" if self.translates else f"Offline translation is not enabled - {ENABLE_TRANSLATION}."
        return {
            "available": self.translates,
            "reason": reason,
            "provider": getattr(self._translator, "name", None) if self._translator is not None else None,
            "listens": self.listens,
            "listener": getattr(self._listener, "name", None) if self.listens else None,
            "listening_reason": "" if self.listens else f"Speech recognition for translation is not enabled - {ENABLE_LISTENING}.",
            "voice": getattr(self._voice, "name", None) if self._voice is not None else None,
            "pairs": [f"{src}->{dst}" for src, dst in sorted(self._translator.pairs())] if self.translates else [],
            "languages": [
                {
                    "code": language.code,
                    "name": language.name,
                    "locale": language.locale,
                    "translatable": language.code in codes,
                    "speakable": bool(self._voice is not None and self._voice.can_speak(language)),
                }
                for language in all_languages()
            ],
        }

    # ---- resolving languages -----------------------------------------------

    def _resolve(self, value: object) -> Language:
        language = language_named(value) or language_by_code(value)
        if language is None:
            raise InterpretationError(f"I don't know a language called '{value}'.")
        return language

    def _installed_names(self) -> str:
        names = sorted(language.name for language in all_languages() if language.code in self._translatable_codes())
        return ", ".join(names) or "none"

    def _require(self, source: Language, target: Language) -> None:
        if source.code != target.code and not self._translator.can_translate(source.code, target.code):
            raise InterpretationError(
                f"I can't translate {source.name} into {target.name} offline - the installed language packs "
                f"cover {self._installed_names()}."
            )

    # ---- text ----------------------------------------------------------------

    def translate_text(self, text: str, target: object, source: object = None) -> Any:
        """Offline translation of ``text`` into ``target`` - ``source`` named or
        guessed. Returns the translator's own result (``text``, ``source``,
        ``target``, ``route``)."""
        if not self.translates:
            raise InterpretationError(f"Offline translation is not enabled - {ENABLE_TRANSLATION}.")
        words = " ".join(str(text or "").split())
        if not words:
            raise InterpretationError("Translate what?")
        destination = self._resolve(target)
        if source is not None and str(source).strip():
            origin = self._resolve(source)
        else:
            origin = self._guessed_origin(words, destination)
        if origin.code == destination.code:
            raise InterpretationError(
                f"that already looks like {destination.name} to me - if it is not, name its language "
                "(\"... from Spanish\")"
            )
        self._require(origin, destination)
        return self._translator.translate(words, origin.code, destination.code)

    def _guessed_origin(self, words: str, destination: Language) -> Language:
        """The language a phrase is *in*, when the person did not name it.

        Falling back to English whenever the guess found nothing was the one
        silent-wrong-answer path in this class: "translate buenas noches into
        English" guessed nothing, assumed English, translated English to
        English, and returned "buenas noches" as a successful translation.
        An unknown source is now said out loud, with the exact way to remove
        the doubt - the same "a misconfiguration must never disguise itself
        as normal operation" rule the rest of this project holds to.
        """
        guessed = guess_language(words, tuple(self._translatable_codes()))
        if guessed is not None:
            return language_by_code(guessed) or language_by_code("en")
        english = language_by_code("en")
        if destination.code != "en" and guess_language(words, ("en",)) == "en":
            return english
        raise InterpretationError(
            "I can't tell what language that is, and I won't guess - name it "
            "(\"... from Spanish\") and I will translate it"
        )

    def speak(self, text: str, language: object) -> "tuple[bytes | None, str, str]":
        """``(wav, voice, spoken_by)`` - the local voice when it speaks this
        language, otherwise "device" (the page uses the device's own voice for
        that locale)."""
        destination = self._resolve(language)
        if self._voice is not None and self._voice.can_speak(destination):
            try:
                spoken = self._voice.synthesize(text, destination)
            except Exception:  # noqa: BLE001 - a local voice failing falls back to the device, honestly
                return None, "", "device"
            return spoken.wav, spoken.voice, "local_voice"
        return None, "", "device"

    # ---- speech --------------------------------------------------------------

    def interpret_speech(
        self, audio: bytes, *, target: object = None, pair: "Sequence[object] | None" = None, speak: object = True,
    ) -> InterpretedTurn:
        """``speak``: True or "local" - the local voice when it speaks the
        language (measured the slow step on this laptop); "device" - no
        synthesis here, the page speaks with the device's own voice; False -
        words only."""
        if not self.listens:
            raise InterpretationError(f"I can't hear speech to translate yet - {ENABLE_LISTENING}.")
        if not self.translates:
            raise InterpretationError(f"Offline translation is not enabled - {ENABLE_TRANSLATION}.")
        if not audio:
            raise InterpretationError("The recording was empty - nothing was heard.")
        started = time.perf_counter()
        try:
            heard = self._listener.recognise(audio)
        except Exception as exc:  # noqa: BLE001 - a recording that cannot be decoded is reported plainly
            raise InterpretationError(f"I couldn't hear that clearly ({exc}).") from exc
        listened = time.perf_counter()
        text = " ".join(str(getattr(heard, "text", "") or "").split())
        if not text:
            raise InterpretationError("I didn't catch any speech in that recording.")
        origin = language_by_code(getattr(heard, "language", ""))
        if origin is None or origin.code not in self._translatable_codes():
            spoken = getattr(heard, "language", "") or "an unknown language"
            raise InterpretationError(
                f"I heard {spoken}, which I can't translate offline - the installed language packs cover "
                f"{self._installed_names()}."
            )
        if pair:
            first, second = (self._resolve(value) for value in list(pair)[:2])
            destination = second if origin.code == first.code else first
        elif target is not None:
            destination = self._resolve(target)
        else:
            raise InterpretationError("Translate into which language?")
        result = self.translate_text(text, destination.code, origin.code)
        translated = time.perf_counter()
        mode = "local" if speak is True else (str(speak).strip().lower() if speak else "none")
        wav, voice, spoken_by = (None, "", "none")
        if mode == "local":
            wav, voice, spoken_by = self.speak(result.text, destination.code)
        elif mode == "device":
            spoken_by = "device"
        finished = time.perf_counter()
        return InterpretedTurn(
            heard=text,
            source=origin.code,
            source_probability=float(getattr(heard, "probability", 0.0) or 0.0),
            translation=result.text,
            target=destination.code,
            route=tuple(result.route),
            audio_wav=wav,
            voice=voice,
            spoken_by=spoken_by,
            seconds={
                "listen": listened - started, "translate": translated - listened,
                "speak": finished - translated, "total": finished - started,
            },
        )
