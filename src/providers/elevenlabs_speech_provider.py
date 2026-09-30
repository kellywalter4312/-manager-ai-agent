"""A second, real speech engine behind the same seam - ElevenLabs, cloud TTS.

Exists to prove the claim ``windows_speech_provider.py`` already makes true
for one engine: that a second one arrives as an additive change, never a
redesign. Nothing in ``src/channels/speech.py`` or ``VoiceProfile`` changed to
add this file; only this module and ``speech_factory.py``'s registry did.

**Discovery is real but deliberately not the same shape as Windows'.**
``WindowsSpeechAdapter.installed_voice_names()`` queries a small, local,
free, already-running engine on every ``speak()`` when nothing is cached -
cheap because it costs one subprocess call to a service already on the
machine. ElevenLabs' voice catalog is neither small nor free to ask about: a
real account can hold hundreds of voices, and every request to list or
synthesise counts against a paid quota. Re-listing the whole catalog before
every utterance to compute a fallback would be genuine waste for no benefit
this provider's one caller needs, so ``speak()`` here does not do it.
``available_voices()`` still exists - the real, live discovery seam a
provider is expected to publish - and is what resolves a voice's stable id
from its name, once, rather than ever guessing one.

**Where the chosen voice lives, and why nothing here names Alfred.** A
``VoiceProfile`` never learns a vendor exists (see that module's own
docstring) - so an ElevenLabs voice id is exactly the kind of provider-
specific fact ``VoiceProfile.style`` already exists to carry, opaque and
unread by anything but the provider that put it there. This module reads
``style["elevenlabs_voice_id"]`` off whatever profile it is given; it has no
knowledge of which agent that profile belongs to, so a future specialist
gets a distinct ElevenLabs voice the same way Alfred does - by declaring one
in its own identity document, not by this file learning a second name.

**Playback.** ElevenLabs returns audio bytes, not sound - there is no
"default output device" call the way SAPI has one. ``_play_wav_bytes`` asks
for a WAV-formatted response (``output_format="wav_24000"``) and plays it
with the standard library's own ``winsound``, the same "no new dependency"
discipline the Windows provider already follows, at the cost of that one
function assuming Windows for now - honestly reported as UNAVAILABLE rather
than crashing this module's import, should this ever run somewhere winsound
does not exist.
"""

from __future__ import annotations

import tempfile
from pathlib import Path
from typing import Any

from src.channels.speech import SpeechAdapter, SpeechUnavailable
from src.providers.availability import Availability, AvailabilityState
from src.services.identity.voice import VoiceProfile
from src.services.identity.voice_options import VoiceOption
from src.utils.logging import get_logger

logger = get_logger(__name__)

# Optional vendor SDK - imported at module level so tests can monkeypatch
# `elevenlabs_speech_provider.ElevenLabs`; None when the package is absent.
try:
    from elevenlabs.client import ElevenLabs
except ImportError:
    ElevenLabs = None  # type: ignore[assignment,misc]

# A real, complete WAV container (RIFF header included) rather than raw PCM -
# so nothing here has to build a header by hand, and so a played file is
# byte-for-byte the same kind of artifact this project's own genuine
# verification already checks (real WAV, real frame count).
_OUTPUT_FORMAT = "wav_24000"


def _voice_id_for(voice: VoiceProfile | None) -> str | None:
    """The stable ElevenLabs voice id this profile carries, if any.

    Read from ``style`` - never a typed ``VoiceProfile`` field - because a
    voice id is exactly the provider-specific fact that field exists to
    hold without ``VoiceProfile`` itself having to know ElevenLabs exists.
    Never derived from ``preferred_voice`` (a display name) by guessing:
    a name is not an id, and this provider does not invent one.
    """
    if voice is None:
        return None
    raw = voice.style.get("elevenlabs_voice_id")
    text = str(raw or "").strip()
    return text or None


def _play_wav_bytes(audio: bytes) -> bool:
    """Play a complete WAV file synchronously, honestly, on this machine.

    Blocking on purpose: ``speak()`` must not return True until the audio has
    actually been produced, the same guarantee the Windows provider's
    ``$synth.Speak()`` already gives by blocking until finished.
    """
    try:
        import winsound
    except ImportError:
        logger.warning("elevenlabs playback requires winsound, which is not available here")
        return False

    path: str | None = None
    try:
        with tempfile.NamedTemporaryFile(suffix=".wav", delete=False) as handle:
            handle.write(audio)
            path = handle.name
        winsound.PlaySound(path, winsound.SND_FILENAME)
        return True
    except OSError as exc:
        logger.warning("elevenlabs playback failed: %s", exc)
        return False
    finally:
        if path is not None:
            Path(path).unlink(missing_ok=True)


class ElevenLabsSpeechAdapter(SpeechAdapter):
    """Text-to-speech via the real ElevenLabs API. Speaking only.

    No ``listen()`` - this project has not asked for ElevenLabs speech
    recognition, and adding it unasked would be exactly the speculative
    abstraction this stage was told not to build. ``listen()`` raises
    honestly, the same stance ``NoSpeech`` already takes for the half of the
    contract it does not implement.
    """

    def __init__(self, api_key: str) -> None:
        if ElevenLabs is None:
            raise ImportError(
                "the elevenlabs package is required for MANAGER_SPEECH=elevenlabs; "
                "run: pip install elevenlabs"
            )
        self._client = ElevenLabs(api_key=api_key)
        self._availability = Availability()

    @property
    def name(self) -> str:
        return "elevenlabs"

    @property
    def availability(self) -> Availability:
        return self._availability

    def probe_authorization(self) -> int:
        """Make the one real call that proves this credential currently
        works - the identical role ``check_authorization`` reaches through
        ``LLMProvider.complete()`` for the reasoning axis, closing the same
        gap for this one (closure campaign, 2026-09-16: 'check' previously
        existed only for ``llm_provider`` - every other governed backend,
        this one included, had construction-only ``validate`` and no way
        to ask "would the vendor actually accept this key?").

        Deliberately **not** ``available_voices()``: that method swallows
        every exception and returns an honestly-empty tuple on ANY
        failure, which is exactly right for a caller that only wants a
        voice list and never wants to crash over it, but wrong for a
        credential check - an empty tuple would then mean both "this key
        is rejected" and "this account genuinely has zero voices",
        indistinguishable. This method lets the real exception through
        instead, so ``check_speech_authorization`` can tell the two apart
        the same honest way ``check_authorization`` already does.

        Returns the real voice count on success. Raises whatever the
        vendor SDK itself raises on failure - never caught here.
        """
        response = self._client.voices.get_all()
        return len(response.voices)

    def listen(self, timeout: float | None = None) -> str:
        raise SpeechUnavailable(
            "the elevenlabs provider is text-to-speech only; it does not "
            "listen. Type instead, or select a provider that hears."
        )

    def available_voices(self) -> "tuple[VoiceOption, ...]":
        """Real, currently-available voices from this account's own
        library, each with whatever language/accent/locale metadata the
        vendor's own API genuinely reports for it - never invented.

        Two real sources, in preference order, per voice: ``verified_languages``
        (``VerifiedVoiceLanguageResponseModel`` - the vendor's own explicit
        per-language records, each already carrying ``language``,
        ``accent`` and ``locale`` as distinct fields) when present, one
        ``VoiceOption`` per verified language so a multilingual voice is
        correctly offered for each language it actually supports rather
        than only one. Falling back to ``labels.get("accent")`` (the
        vendor's own free-text label dict) with ``language=None`` when a
        voice reports no verified languages at all - an honest partial
        fact, never a guessed language.

        The live discovery seam - what a person or the ``select_voice``
        capability actually calls to see what is genuinely available, the
        same role ``installed_voice_names()`` plays for Windows. Never
        cached: unlike a local engine's fixed roster, an account's
        ElevenLabs library can change between calls, and pretending
        otherwise would be the same mistake ``Availability`` already
        exists to prevent - stale evidence presented as current.
        """
        try:
            response = self._client.voices.get_all()
        except Exception as exc:  # noqa: BLE001 - a paid HTTP API fails in more ways than OSError
            logger.warning("elevenlabs voice catalog could not be listed: %s", exc)
            return ()

        options: list[VoiceOption] = []
        for v in response.voices:
            if not v.voice_id or not v.name:
                continue
            # Voice-level facts - read once per voice, independent of which
            # verified language entry (if any) is being expanded below. Both
            # were previously read only in the labels-only branch, which
            # meant a multilingual voice with verified_languages silently
            # lost its gender and never carried a style at all - found and
            # fixed alongside U-VOICE-05, the same real ``labels`` dict.
            labels = getattr(v, "labels", None) or {}
            gender = str(labels.get("gender") or "").strip().lower() or None
            style = str(labels.get("descriptive") or "").strip().lower() or None
            verified = getattr(v, "verified_languages", None) or ()
            if verified:
                for entry in verified:
                    language = str(entry.language).strip() if entry.language else None
                    options.append(
                        VoiceOption(
                            voice_id=v.voice_id, name=v.name, provider=self.name,
                            language=language or None,
                            accent=str(entry.accent).strip() if entry.accent else None,
                            locale=str(entry.locale).strip() if entry.locale else None,
                            gender=gender, style=style,
                        )
                    )
            else:
                accent = str(labels.get("accent") or "").strip() or None
                options.append(
                    VoiceOption(voice_id=v.voice_id, name=v.name, provider=self.name,
                                accent=accent, gender=gender, style=style)
                )
        return tuple(options)

    # ElevenLabs' multilingual model speaks the language of the text it is
    # given in any voice, keeping that voice's own accent.
    speaks_across_languages = True

    def spoken_languages(self) -> "tuple[str, ...]":
        """The languages the account's voices are verified to speak, as ElevenLabs reports them."""
        return tuple(sorted({o.language for o in self.available_voices() if o.language}))

    def speaking_styles(self) -> "tuple[str, ...]":
        """The speaking-style labels this account's voices are genuinely
        tagged with - ElevenLabs' own ``labels["descriptive"]`` per voice
        ("calm", "confident", "excited", ...), the same real label dict
        ``available_voices`` already reads ``accent``/``gender`` from.
        Never a fixed catalogue this module invents: an account whose
        voices carry no descriptive label reports none, honestly, the
        same as a local engine with no styles at all.
        """
        return tuple(sorted({o.style for o in self.available_voices() if o.style}))

    def voice_in_use(self, voice: VoiceProfile | None) -> "str | None":
        """The ElevenLabs voice id ``synthesize`` will actually use for ``voice``."""
        return _voice_id_for(voice)

    def synthesize(self, text: str, voice: VoiceProfile | None = None) -> bytes:
        voice_id = _voice_id_for(voice)
        if voice_id is None:
            raise SpeechUnavailable(
                f"no elevenlabs voice id configured for '{voice.key}'" if voice is not None
                else "no voice profile supplied and elevenlabs has no default voice"
            )
        chunks: Any = self._client.text_to_speech.convert(voice_id, text=text, output_format=_OUTPUT_FORMAT)
        return b"".join(chunks)

    def speak(self, text: str, voice: VoiceProfile | None = None) -> bool:
        try:
            audio = self.synthesize(text, voice)
        except SpeechUnavailable as exc:
            self._availability = Availability(state=AvailabilityState.UNAVAILABLE, reason=str(exc))
            return False
        except Exception as exc:  # noqa: BLE001 - a paid HTTP API fails in more ways than OSError
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
