"""A real, user-directed browser recording - an actual audio or video
artifact, never text pretending to be one.

**T-REC-03 (2026-09-22): broadened from audio alone to screen and camera
capture.** All three are the identical real request shape this module
already existed to recognise apart from ``capture.py`` - an explicit,
deterministic request to capture *live media*, distinguished from each
other only by which device the frontend's own ``getUserMedia``/
``getDisplayMedia`` call actually opens (see ``ui/index.html``'s
``startRecording``). One recognition module, one ``RecordingSessionRegistry``
lifecycle (``recording_session.py``, now carrying a ``kind``), one pair of
governed Memory categories (``audio_recording``/``video_recording`` -
screen and camera share the second, distinguished by
``context["source"]``) - never three parallel systems for what is, at
every layer beneath the actual device request, the same mechanism.

**Kept apart from "record this" (``capture.py``) on purpose.** That module
recognises an explicit request to capture *words already said* as a durable
note; this module recognises an explicit request to capture *microphone
audio*. The two occasionally share a verb ("record") but never the same
request shape, and where a phrase is genuinely ambiguous between them
(``capture.py``'s own "record this"/"keep this" markers), that existing,
already-tested capture behaviour wins rather than being silently
reinterpreted - a person who has relied on "record this for later" meaning
a text note for years must not wake up to it meaning something else. This
module's own markers below are chosen specifically to avoid that overlap
(see ``_START_MARKERS``' own comment).

**Deterministic, no model, no guessing - the identical discipline every
other marker-based intent recognizer in this project already holds
itself to** (``capture.py``, ``tasks.py``). A small, extensible set of
phrasings recognised by shape, never a closed list of exact sentences and
never one branch per phrase - a future synonym is one more string in the
tuple below, not a new code path.

**What this module does NOT do.** It never touches a microphone or an
``ArtifactStore``, and it never *writes* to Memory - Python has no route
to a browser's microphone at all, so the actual capture is necessarily a
frontend concern (see ``ui/index.html``'s own recording UI). Most of this
module only recognises the *shape* of a request ("start recording,
optionally for a purpose" / "stop recording" / "cancel/discard the
recording") and extracts the purpose named, if any - the same neutral,
agent-agnostic role ``capture.py``/``tasks.py`` already play, so a
Manager capability and a future specialist's own request handling can
both recognise the identical shape without either owning it.

``build_recording_record`` is the one exception, and only a builder:
exactly like ``build_task_record``/``build_recording`` in the sibling
modules, it turns already-known facts (a purpose, a real artifact id)
into a candidate ``MemoryRecord`` - never remembers it. The caller
(today, ``scripts/serve_ui.py``'s own save route) decides whose governed
``retain`` the candidate is offered to. This is also what keeps
``scripts/serve_ui.py`` itself honest: that entry point is guarded
(``tests/test_live_ui.py``) against importing ``src.services.memory``
directly at all - it may only ever reach a *candidate record* through a
builder function like this one, never construct a ``MemoryRecord``
itself.
"""

from __future__ import annotations

import re

from src.services.memory.memory import MemoryRecord

DEFAULT_RECORDING_PURPOSE = "general"
AUDIO_RECORDING_KIND = "audio_recording"
# Screen and camera capture share one governed Memory category - both are
# "a real video the browser captured", distinguished by context["source"]
# ("screen"/"camera") rather than a third/fourth parallel category.
VIDEO_RECORDING_KIND = "video_recording"

# Chosen specifically so none of these are also substrings capture.py's own
# _CAPTURE_FOR/_CAPTURE_PLAIN would match ("record this"/"keep this") -
# every marker here either names "recording"/"audio"/"voice" explicitly, or
# uses a verb ("capture", "start", "begin") capture.py's own markers never
# use. That is what lets both modules claim their own request shape without
# one phrase ever satisfying both.
_AUDIO_START_MARKERS: tuple[str, ...] = (
    "start recording", "start a recording", "start an audio recording",
    "start a voice recording", "start voice recording", "begin recording",
    "begin a recording", "begin an audio recording", "record audio",
    "record my voice", "record what i'm saying", "record what i am saying",
    "record what i say", "make an audio recording", "make a voice recording",
    "make an audio note", "start an audio note", "start a voice note",
    "start a voice memo", "capture this for", "capture what i'm saying",
    "capture what i am saying", "capture my voice",
)
# T-REC-03: the identical deterministic, marker-by-shape recognition the
# audio markers above already use, named specifically enough ("my screen",
# "this screen") that they never collide with a plain "record" no kind is
# named for - an unqualified "start recording" stays audio, exactly as it
# always has.
_SCREEN_START_MARKERS: tuple[str, ...] = (
    "record my screen", "record the screen", "record this screen",
    "start recording my screen", "start recording the screen",
    "start a screen recording", "start screen recording",
    "begin a screen recording", "begin screen recording",
    "capture my screen", "capture the screen", "share and record my screen",
)
_CAMERA_START_MARKERS: tuple[str, ...] = (
    "record my camera", "record from my camera", "record with my camera",
    "start recording my camera", "start a camera recording", "start camera recording",
    "begin a camera recording", "begin camera recording",
    "record a video of myself", "start a video recording",
    "start video recording", "make a video recording",
)
_START_MARKERS: tuple[str, ...] = _AUDIO_START_MARKERS + _SCREEN_START_MARKERS + _CAMERA_START_MARKERS
_STOP_MARKERS: tuple[str, ...] = (
    "stop recording", "stop the recording", "stop this recording",
    "end recording", "end the recording", "finish recording",
    "finish the recording", "stop the audio recording",
    "stop the voice recording", "stop the screen recording",
    "stop screen recording", "stop the camera recording",
    "stop camera recording", "stop the video recording", "stop video recording",
)
_CANCEL_MARKERS: tuple[str, ...] = (
    "cancel recording", "cancel the recording", "cancel this recording",
    "discard the recording", "discard this recording", "throw away the recording",
    "never mind the recording", "abort the recording",
)
# The identical "bare listing vs. a named topic" shape tasks.py's own
# _LIST_MARKERS/_QUERY_MARKERS already use, applied to recordings.
_LIST_MARKERS: tuple[str, ...] = (
    "what recordings do i have", "show my recordings", "list my recordings",
    "what are my recordings", "my recordings",
)
_QUERY_MARKERS: tuple[str, ...] = (
    "find the recording", "find a recording", "play the recording",
    "do i have a recording about", "is there a recording about",
    "what recordings do i have about",
)

# The same "whatever follows for *is* the purpose" extraction capture.py's
# own docstring already establishes for capture, applied here independent
# of which start marker matched - a person's own phrasing of "for X" varies
# far more than the fixed verb phrase in front of it does.
_FOR_PURPOSE = re.compile(
    r"\bfor\s+(?:the\s+)?([^.!?:]+?)(?:[.!?:]|$)", re.IGNORECASE
)


def _marker_present(text: str, markers: tuple[str, ...]) -> bool:
    lowered = " ".join(str(text or "").lower().split())
    return any(marker in lowered for marker in markers)


def start_recording_intent(text: str) -> bool:
    """The ``claims=`` predicate for beginning a new recording - audio,
    screen or camera; ``recording_kind_of`` says which."""
    return _marker_present(text, _START_MARKERS)


def recording_kind_of(text: str) -> str:
    """Which real capture an explicit start-recording request named -
    ``"screen"``, ``"camera"``, or the honest default ``"audio"`` when
    neither was named (an unqualified "start recording" has always meant
    the microphone, and still does). Checked before the plain audio
    markers so a phrase that happens to satisfy both a screen/camera
    marker and a looser audio one (there are none today, but a future
    marker could) resolves to the more specific kind named."""
    if _marker_present(text, _SCREEN_START_MARKERS):
        return "screen"
    if _marker_present(text, _CAMERA_START_MARKERS):
        return "camera"
    return "audio"


def stop_recording_intent(text: str) -> bool:
    """The ``claims=`` predicate for stopping the current recording."""
    return _marker_present(text, _STOP_MARKERS)


def cancel_recording_intent(text: str) -> bool:
    """The ``claims=`` predicate for discarding the current recording
    without saving it."""
    return _marker_present(text, _CANCEL_MARKERS)


def extract_recording_purpose(text: str) -> str:
    """The purpose named in an explicit start-recording request, or
    :data:`DEFAULT_RECORDING_PURPOSE` when none was given - the identical
    "never fabricated, honestly generic when nothing was said" default
    ``capture.py``'s own ``DEFAULT_PURPOSE`` already establishes.
    """
    match = _FOR_PURPOSE.search(str(text or ""))
    if match is None:
        return DEFAULT_RECORDING_PURPOSE
    purpose = " ".join(match.group(1).split())
    return purpose or DEFAULT_RECORDING_PURPOSE


def list_recordings_intent(text: str) -> bool:
    """The ``claims=`` predicate for listing/querying saved recordings -
    the identical bare-listing-or-named-topic shape ``list_tasks_intent``
    already uses."""
    return _marker_present(text, _LIST_MARKERS) or _marker_present(text, _QUERY_MARKERS)


def extract_recording_query(text: str) -> str:
    """The topic to filter by, or "" for "list everything" - the
    identical shape ``extract_task_query`` already has."""
    lowered = " ".join(str(text or "").split())
    working = lowered.lower()
    for marker in _QUERY_MARKERS:
        position = working.find(marker)
        if position == -1:
            continue
        remainder = lowered[position + len(marker):].strip(" ,.:;?\t\n")
        remainder = re.sub(r"^(?:to|that|about|:|-)\s*", "", remainder, flags=re.IGNORECASE).strip(" ,.:;?\t\n")
        if remainder:
            return remainder
    return ""


def match_recordings_by_topic(
    candidates: "tuple[MemoryRecord, ...]", topic: str
) -> "tuple[MemoryRecord, ...]":
    """Which of ``candidates`` a person naming ``topic`` most plausibly
    means - exact purpose match first, then a substring either
    direction against the purpose, then the same against the full
    content line. The identical deterministic, no-token-overlap
    matching ``match_tasks_by_topic`` already uses, and for the
    identical reason: token overlap would let "a recording about the
    budget" and "a recording about the plants" collide on the shared
    token "the"."""
    topic_lower = " ".join(topic.strip().lower().split())
    if not topic_lower:
        return ()

    def purpose_of(record: MemoryRecord) -> str:
        return str((record.context or {}).get("purpose") or "").lower()

    exact = tuple(r for r in candidates if purpose_of(r) == topic_lower)
    if exact:
        return exact
    by_purpose = tuple(
        r for r in candidates
        if topic_lower in purpose_of(r) or purpose_of(r) in topic_lower
    )
    if by_purpose:
        return by_purpose
    return tuple(
        r for r in candidates
        if topic_lower in r.content.lower() or r.content.lower() in topic_lower
    )


# capture kind -> (memory kind, tag prefix, the noun a description uses)
_KIND_FACTS: "dict[str, tuple[str, str]]" = {
    "audio": (AUDIO_RECORDING_KIND, "Voice"),
    "screen": (VIDEO_RECORDING_KIND, "Screen"),
    "camera": (VIDEO_RECORDING_KIND, "Camera"),
}


def build_recording_record(
    *,
    purpose: str,
    artifact_id: str,
    created_by: str,
    duration_seconds: "float | int | None" = None,
    mime_type: str = "",
    rendition_format: str = "",
    capture_kind: str = "audio",
) -> MemoryRecord:
    """One candidate recording-reference record for a real, already-
    saved artifact - the identical builder role ``build_task_record``/
    ``build_recording`` already play for their own governed categories.
    Never writes anything; the caller decides whose ``retain`` this is
    offered to.

    ``artifact_id`` must already name a real, registered artifact - this
    function does not create one, and never invents an id. ``capture_kind``
    (``"audio"``/``"screen"``/``"camera"``) decides which governed category
    this lands in (screen and camera share ``video_recording``,
    distinguished by ``context["source"]``) and which noun describes it -
    T-REC-03's own addition, an unrecognised kind falls back to ``"audio"``
    honestly rather than raising.
    """
    purpose_clean = " ".join(str(purpose or "").split()) or DEFAULT_RECORDING_PURPOSE
    duration_text = (
        f" ({int(duration_seconds)}s)"
        if isinstance(duration_seconds, (int, float)) and duration_seconds
        else ""
    )
    memory_kind, noun = _KIND_FACTS.get(str(capture_kind or "").lower(), _KIND_FACTS["audio"])
    source = str(capture_kind or "").lower() if str(capture_kind or "").lower() in _KIND_FACTS else "audio"
    tag = f"{memory_kind}:{artifact_id}"
    return MemoryRecord(
        content=f"{noun} recording for {purpose_clean}{duration_text}",
        kind=memory_kind,
        tags={memory_kind, tag},
        context={
            "fact_type": memory_kind,
            "source": source,
            "artifact_id": artifact_id,
            "purpose": purpose_clean,
            "duration_seconds": duration_seconds,
            "mime_type": mime_type,
            "rendition_format": rendition_format,
            "created_by": created_by,
            # Full confidence, the identical reasoning capture.py's own
            # build_recording already documents: a real, user-directed
            # recording that genuinely happened is not a tentative
            # observation - it is exactly what it says it is. Read by
            # SpecialistMemoryPolicy's own confidence gate (T-REC-03: found
            # missing here even after admitting the category itself, which
            # is why no specialist's recording was ever actually retained
            # before this repair, silently defaulting to 0.0 and refused).
            "confidence": 1.0,
            # Honest, not fabricated: no transcription engine is
            # configured in this project yet (see this module's own
            # docstring on why recording and transcription stay separate
            # capabilities) - "not_available" describes a real system
            # state, never a guessed transcript.
            "transcription_status": "not_available",
        },
    )


def describe_recording(record: MemoryRecord) -> str:
    """One recording, rendered for a person to read - the identical
    shape a Control Center listing and a conversational answer both
    use, so the two surfaces never describe the same recording two
    different ways."""
    context = record.context or {}
    purpose = str(context.get("purpose") or DEFAULT_RECORDING_PURPOSE)
    duration = context.get("duration_seconds")
    artifact_id = str(context.get("artifact_id") or "")
    duration_text = f", {int(duration)}s" if isinstance(duration, (int, float)) and duration else ""
    return f"[{purpose}{duration_text}] {record.content} ({artifact_id})"
