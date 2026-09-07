"""Real speech-to-text over locally-extracted audio - genuine audio
*understanding*, distinct from every other module in this package.
``video.py`` composes; ``video_generation.py`` asks a vendor to
synthesise; ``video_clipping.py`` cuts a real file at real, already-known
timestamps. None of them derive anything by actually listening to a
video's own content. This module is the missing piece: turning a
video's real, extracted audio (``video.py::extract_audio_wav``) into a
real, timestamped transcript, so a later step
(``src/manager/capabilities.py::clip_video``) can reason over what the
video *actually contains* rather than only over text the user happened
to type separately.

The identical shape every other real-vendor provider family in this
project already takes: an abstract contract, an honest null default,
and one real implementation - here, an adapter for a mature, external,
credentialed speech-to-text vendor, because CHECK found no genuinely
free or keyless equivalent to ``PollinationsImageProvider`` for
production-quality transcription.

**A dedicated axis, never borrowed from the LLM's own vendor.** OpenAI
happens to supply both this project's optional GPT-backed LLM provider
and the Whisper transcription vendor chosen here - the identical "one
company, several independent axes" situation ``RUNWAY_KEY_ENV``/
``VEO_KEY_ENV`` already document for video generation (see
``factory.py``'s own comments there). ``WHISPER_API_KEY`` is its own,
dedicated credential, read nowhere else in this project, so a
deployment can run Whisper transcription against one OpenAI account
while its LLM reasoning runs on a different vendor entirely (or none)
without either choice touching the other.

**Segments, not a flat blob.** A ``Transcript`` is ordered, timestamped
``TranscriptSegment``s - real evidence a later Brain call is asked to
select *from* ("which of these actually-spoken segments matches the
request"), never text so unanchored that "propose a time range" would
amount to a guess dressed up as understanding. This is what makes the
eventual ``clip_video`` call genuine content understanding rather than
a second place to fabricate timestamps.

**Why this reaches production before any credential is funded.** The
identical argument ``RunwayVideoProvider``/``GoogleVeoVideoProvider``'s
own docstrings already make: the mechanism - credential wiring, request
construction, multipart upload, response parsing, error handling - is
independently valuable and independently verifiable without a live
vendor response. "Mechanism implemented and tested" is recorded as
distinct from "live vendor authorisation tested" throughout this
module and its tests, never conflated.
"""

from __future__ import annotations

import json
import urllib.error
import urllib.request
import uuid
from abc import ABC, abstractmethod
from dataclasses import dataclass
from typing import Callable, Mapping


class TranscriptionError(RuntimeError):
    """Raised when real speech-to-text could not be produced."""


@dataclass(frozen=True)
class TranscriptSegment:
    """One real, timestamped span of speech - the atomic unit of
    evidence a later Brain call reasons over, never a flat, unanchored
    blob of text."""

    start_seconds: float
    end_seconds: float
    text: str


def _format_timecode(seconds: float) -> str:
    total = max(0, int(round(seconds)))
    minutes, secs = divmod(total, 60)
    return f"{minutes}:{secs:02d}"


@dataclass(frozen=True)
class Transcript:
    """A real video/audio file's actual spoken content, as ordered,
    timestamped segments - never fabricated, and never fewer segments
    than the vendor actually reported."""

    segments: tuple[TranscriptSegment, ...]
    language: str = ""
    provider: str = ""
    model: str = ""

    def __post_init__(self) -> None:
        if not self.segments:
            raise TranscriptionError("Transcript.segments must not be empty")

    def as_timestamped_text(self) -> str:
        """Render as one ``'MM:SS-MM:SS: text'`` line per segment - the
        exact, grounded shape ``src.manager.capabilities.clip_video``
        feeds to the Brain, so it selects real spoken moments rather
        than proposing boundaries with no anchor in the video's own
        actual content."""
        return "\n".join(
            f"{_format_timecode(s.start_seconds)}-{_format_timecode(s.end_seconds)}: {s.text.strip()}"
            for s in self.segments
        )


class TranscriptionProvider(ABC):
    """A source of real, timestamped speech-to-text over real audio bytes."""

    @property
    @abstractmethod
    def name(self) -> str:
        """Short identifier, used in logs and configuration."""

    @abstractmethod
    def transcribe(self, audio: bytes) -> Transcript:
        """Return the real, timestamped transcript of ``audio``.

        Implementations must raise ``TranscriptionError`` on empty
        audio or a genuine transcription failure - never return an
        empty transcript or invented text standing in for speech that
        was never actually decoded.
        """

    def __repr__(self) -> str:
        return f"<{type(self).__name__} name={self.name!r}>"


class NullTranscriptionProvider(TranscriptionProvider):
    """The default: no transcription vendor configured. Honest, not
    silent - the same role every other Null* provider in this project
    already plays."""

    @property
    def name(self) -> str:
        return "null"

    def transcribe(self, audio: bytes) -> Transcript:
        raise TranscriptionError(
            "no speech-transcription provider is configured, so I cannot "
            "derive a real transcript from this audio - I would rather say "
            "so than invent one"
        )


@dataclass(frozen=True)
class _HTTPResponse:
    """One HTTP exchange's real outcome - the identical injectable seam
    ``video_generation.py``'s own ``_HTTPResponse``/``Transport`` already
    establish, redeclared here rather than imported so this module stays
    independently replaceable (see this module's own docstring)."""

    status: int
    headers: dict
    body: bytes


Transport = Callable[[str, str, "dict | None", "bytes | None"], _HTTPResponse]


def _encode_multipart(
    boundary: str,
    fields: Mapping[str, str],
    *,
    file_field: str,
    filename: str,
    file_bytes: bytes,
    file_content_type: str,
) -> bytes:
    """A minimal, real ``multipart/form-data`` body - no third-party
    dependency, matching this project's own "stdlib first" discipline
    every other real HTTP adapter here already follows."""
    parts: list[bytes] = []
    for field_name, value in fields.items():
        parts.append(f"--{boundary}".encode())
        parts.append(f'Content-Disposition: form-data; name="{field_name}"'.encode())
        parts.append(b"")
        parts.append(str(value).encode("utf-8"))
    parts.append(f"--{boundary}".encode())
    parts.append(
        f'Content-Disposition: form-data; name="{file_field}"; filename="{filename}"'.encode()
    )
    parts.append(f"Content-Type: {file_content_type}".encode())
    parts.append(b"")
    parts.append(file_bytes)
    parts.append(f"--{boundary}--".encode())
    parts.append(b"")
    return b"\r\n".join(parts)


class WhisperTranscriptionProvider(TranscriptionProvider):
    """Real adapter for OpenAI's Whisper speech-to-text API
    (``api.openai.com/v1/audio/transcriptions``) - chosen as the
    genuinely established, production-grade transcription vendor, with
    a simple, single-credential (Bearer token) auth model and a plain,
    synchronous request/response shape (no async job polling, unlike
    both video-*generation* vendors in ``video_generation.py``).

    Requests ``response_format=verbose_json`` so the vendor's own
    response carries real per-segment start/end timestamps - the exact
    grounded, timestamped shape ``Transcript`` requires. A vendor
    response with no segments is treated as a genuine failure, never
    silently degraded to one flat, unanchored segment covering the
    whole file.

    ``transport`` is injectable, the identical dependency-injection seam
    ``RunwayVideoProvider``/``GoogleVeoVideoProvider`` already use for
    their own vendor calls: a real HTTP request in production, a
    controlled double in every test - this project's test suite blocks
    all outbound network unconditionally (``tests/conftest.py::
    _no_network``), so the real vendor exchange remains to be verified
    live, outside the suite, exactly as both unfunded video-generation
    vendors already are.
    """

    BASE_URL = "https://api.openai.com/v1"
    DEFAULT_MODEL = "whisper-1"

    def __init__(
        self,
        api_key: str,
        model: str = DEFAULT_MODEL,
        *,
        transport: "Transport | None" = None,
    ) -> None:
        if not api_key.strip():
            raise TranscriptionError("an OpenAI API key must be supplied for Whisper transcription")
        self._api_key = api_key
        self._model = model
        self._transport = transport or self._real_transport

    @property
    def name(self) -> str:
        return "whisper"

    def _real_transport(
        self, method: str, url: str, headers: "dict | None", body: "bytes | None"
    ) -> _HTTPResponse:
        request = urllib.request.Request(url, data=body, method=method, headers=headers or {})
        try:
            with urllib.request.urlopen(request, timeout=120) as response:
                return _HTTPResponse(status=response.status, headers=dict(response.headers), body=response.read())
        except urllib.error.HTTPError as exc:
            return _HTTPResponse(status=exc.code, headers=dict(exc.headers or {}), body=exc.read())

    def transcribe(self, audio: bytes) -> Transcript:
        if not audio:
            raise TranscriptionError("audio must not be empty")

        boundary = uuid.uuid4().hex
        body = _encode_multipart(
            boundary,
            {"model": self._model, "response_format": "verbose_json"},
            file_field="file",
            filename="audio.wav",
            file_bytes=audio,
            file_content_type="audio/wav",
        )
        headers = {
            "Authorization": f"Bearer {self._api_key}",
            "Content-Type": f"multipart/form-data; boundary={boundary}",
        }
        response = self._transport("POST", f"{self.BASE_URL}/audio/transcriptions", headers, body)
        if response.status >= 400:
            raise TranscriptionError(
                f"Whisper rejected the transcription request (HTTP {response.status}): "
                f"{response.body[:300]!r}"
            )

        try:
            data = json.loads(response.body)
        except (ValueError, TypeError) as exc:
            raise TranscriptionError(f"Whisper returned an unreadable response: {exc}") from exc

        segments_data = data.get("segments") or []
        if not segments_data:
            raise TranscriptionError("Whisper reported success but returned no timestamped segments")

        try:
            segments = tuple(
                TranscriptSegment(
                    start_seconds=float(seg["start"]),
                    end_seconds=float(seg["end"]),
                    text=str(seg.get("text", "")),
                )
                for seg in segments_data
            )
        except (KeyError, TypeError, ValueError) as exc:
            raise TranscriptionError(f"Whisper returned an unreadable segment: {exc}") from exc

        return Transcript(
            segments=segments,
            language=str(data.get("language", "")),
            provider=self.name,
            model=self._model,
        )
